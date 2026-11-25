"""End-to-end coverage for the core inventory workflow on isolated SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


_test_data = tempfile.TemporaryDirectory(prefix="plaza-vea-phase11-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_test_data.name, 'phase11.sqlite3').as_posix()}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["REPLENISHMENT_MIN_HISTORY_DAYS"] = "1"
os.environ["REPLENISHMENT_MIN_POSITIVE_SALES_DAYS"] = "1"

import uvicorn
import main
import database
from database import connection
from inventory_policy import inventory_status
from pydantic import ValidationError


class CoreUnitTests(unittest.TestCase):
    def test_inventory_status_thresholds(self):
        self.assertEqual(inventory_status(0, 10, 3, 30), "Stock crítico")
        self.assertEqual(inventory_status(8, 10, 3, 30), "Stock bajo")
        self.assertEqual(inventory_status(20, 10, 3, 30), "Stock normal")
        self.assertEqual(inventory_status(31, 10, 3, 30), "Exceso de inventario")
        self.assertEqual(inventory_status(20, None, None, None), "Política pendiente")

    def test_product_policy_rejects_inconsistent_thresholds(self):
        with self.assertRaises(ValidationError):
            main.ProductIn(code="QA-1", name="Arroz", category="Abarrotes", price=5, stock=100,
                           unit_of_measure="unidades", minimum_stock=20, safety_stock=25,
                           maximum_stock=30, supplier="Proveedor QA", lead_time_days=2)


class Phase11ApiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.socket.bind(("127.0.0.1", 0))
        cls.socket.listen(128)
        cls.base_url = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(main.app, log_level="error", lifespan="on"))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={"sockets": [cls.socket]}, daemon=True)
        cls.thread.start()
        deadline = time.time() + 15
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("El backend aislado no inició para las pruebas.")

        cls.admin_email = "qa-admin@plazavea.test"
        cls.admin_password = "Pruebas-QA-2026!"
        with connection() as db:
            db.execute("INSERT INTO users(email,display_name,password_hash,is_active,role,created_at) VALUES(?,?,?,1,'Administrador',?)",
                       (cls.admin_email, "Administración QA", main.make_password_hash(cls.admin_password), main.now().isoformat()))
        cls.admin = cls.login(cls.admin_email, cls.admin_password)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        database.get_engine().dispose()
        _test_data.cleanup()

    @classmethod
    def login(cls, email: str, password: str):
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        payload = json.dumps({"email": email, "password": password}).encode()
        request = Request(f"{cls.base_url}/api/auth/login", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(request, timeout=5) as response:
            user = json.loads(response.read())["user"]
        return opener, user

    def request(self, path: str, method="GET", payload=None, opener=None):
        opener = opener or self.admin[0]
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(f"{self.base_url}{path}", data=body,
                          headers={"Content-Type": "application/json"} if body is not None else {}, method=method)
        try:
            with opener.open(request, timeout=5) as response:
                content = response.read()
                return response.status, json.loads(content) if content else None
        except HTTPError as error:
            content = error.read()
            return error.code, json.loads(content) if content else None

    def test_role_without_module_cannot_read_module_endpoint(self):
        email, password = "qa-sales-only@plazavea.test", "Solo-Ventas-2026!"
        with connection() as db:
            db.execute("INSERT INTO roles(name,description,can_manage_users) VALUES(?,?,0)", ("QA Ventas", "Rol aislado de pruebas",))
            db.execute("INSERT INTO role_modules(role_name,module_key) VALUES('QA Ventas','sales')")
            db.execute("INSERT INTO users(email,display_name,password_hash,is_active,role,created_at) VALUES(?,?,?,1,'QA Ventas',?)",
                       (email, "Usuario ventas QA", main.make_password_hash(password), main.now().isoformat()))
        opener, user = self.login(email, password)
        self.assertEqual(user["modules"], ["sales"])
        status, body = self.request("/api/dashboard", opener=opener)
        self.assertEqual(status, 403, body)

    def test_end_to_end_sale_alert_replenishment_and_traceability(self):
        today = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
        product_status, product = self.request("/api/products", "POST", {
            "code": "QA-PHASE11-001", "name": "Arroz de prueba", "category": "Abarrotes",
            "price": 4.2, "stock": 100, "unit_of_measure": "unidades", "minimum_stock": 100,
            "safety_stock": 100, "maximum_stock": 200, "supplier": "Proveedor de prueba", "lead_time_days": 1,
        })
        self.assertEqual(product_status, 201, product)
        product_id = product["id"]
        self.assertEqual(product["stock"], 100)

        sale_status, sale = self.request("/api/sales", "POST", {
            "reference": "QA-PHASE11-SALE-001",
            "occurred_at": datetime.now(timezone(timedelta(hours=-5))).isoformat(),
            "items": [{"product_id": product_id, "quantity": 10, "unit_price": 4.2}],
        })
        self.assertEqual(sale_status, 201, sale)
        self.assertEqual(sale["total"], 42.0)

        status, catalog = self.request("/api/inventory/products")
        self.assertEqual(status, 200)
        self.assertEqual(next(row for row in catalog if row["id"] == product_id)["stock"], 90)

        sale_ledger_params = urlencode({"from_date": today, "to_date": today, "product_id": product_id, "page_size": 100, "movement_type": "Salida por venta"})
        status, ledger = self.request(f"/api/inventory/movements?{sale_ledger_params}")
        self.assertEqual(status, 200, ledger)
        sale_movement = next(row for row in ledger["items"] if row["reference"] == "QA-PHASE11-SALE-001")
        self.assertEqual((sale_movement["stock_before"], sale_movement["stock_after"]), (100, 90))
        self.assertEqual(sale_movement["user_id"], self.admin[1]["id"])

        sales_params = urlencode({"from_date": today, "to_date": today, "product_id": product_id, "page_size": 100})
        status, sales = self.request(f"/api/sales?{sales_params}")
        self.assertEqual(status, 200, sales)
        self.assertEqual(sales["total"], 1)
        self.assertEqual(sales["items"][0]["units"], 10)

        dashboard_params = urlencode({"from_date": today, "to_date": today, "product_id": product_id})
        status, dashboard = self.request(f"/api/dashboard?{dashboard_params}")
        self.assertEqual(status, 200, dashboard)
        self.assertEqual(dashboard["summary"]["total_sales"], 42.0)
        self.assertEqual(dashboard["summary"]["units_sold"], 10)

        status, alerts = self.request("/api/alerts?status=active&alert_type=stock_critical&page_size=100")
        self.assertEqual(status, 200, alerts)
        self.assertTrue(any(alert["product_id"] == product_id for alert in alerts["items"]))

        status, suggestion_page = self.request("/api/replenishment/suggestions")
        self.assertEqual(status, 200, suggestion_page)
        suggestion = next(row for row in suggestion_page["items"] if row["product_id"] == product_id)
        self.assertEqual(suggestion["status"], "needed")
        self.assertEqual(suggestion["suggested_quantity"], 20)
        request_status, replenishment = self.request("/api/replenishment/requests", "POST", {"product_id": product_id, "note": "Solicitud de prueba aislada"})
        self.assertEqual(request_status, 201, replenishment)
        self.assertEqual(replenishment["status"], "pending_review")

        decision_status, approved = self.request(f"/api/replenishment/requests/{replenishment['id']}/decision", "POST", {"decision": "approve", "note": "Aprobación de prueba"})
        self.assertEqual(decision_status, 200, approved)
        self.assertEqual(approved["status"], "approved")
        status, catalog = self.request("/api/inventory/products")
        self.assertEqual(next(row for row in catalog if row["id"] == product_id)["stock"], 90, "Aprobar no debe cambiar stock.")

        receipt_status, received = self.request(f"/api/replenishment/requests/{replenishment['id']}/receipts", "POST", {"quantity": 20, "reference": "QA-PHASE11-RECEIPT-001"})
        self.assertEqual(receipt_status, 201, received)
        self.assertEqual(received["status"], "received")
        self.assertEqual(received["received_quantity"], 20)
        status, catalog = self.request("/api/inventory/products")
        self.assertEqual(next(row for row in catalog if row["id"] == product_id)["stock"], 110)

        receipt_ledger_params = urlencode({"from_date": today, "to_date": today, "product_id": product_id, "page_size": 100, "movement_type": "Entrada por recepción"})
        status, ledger = self.request(f"/api/inventory/movements?{receipt_ledger_params}")
        self.assertEqual(status, 200, ledger)
        receipt_movement = next(row for row in ledger["items"] if row["reference"] == "QA-PHASE11-RECEIPT-001")
        self.assertEqual((receipt_movement["stock_before"], receipt_movement["stock_after"]), (90, 110))
        self.assertEqual(receipt_movement["user_id"], self.admin[1]["id"])

        adjustment_status, adjustment = self.request("/api/inventory/movements", "POST", {
            "product_id": product_id, "movement_type": "Ajuste positivo", "quantity": 1,
            "reason": "Comprobación de auditoría automatizada", "reference": "QA-PHASE11-ADJUST-001",
        })
        self.assertEqual(adjustment_status, 201, adjustment)
        self.assertEqual(adjustment["user_id"], self.admin[1]["id"])

        status, audit = self.request("/api/admin/audit?page_size=100")
        self.assertEqual(status, 200, audit)
        actions = {event["action"] for event in audit["items"]}
        for expected in ("product.created", "sale.created", "inventory.movement_recorded", "replenishment.request_created", "replenishment.approved", "replenishment.received"):
            self.assertIn(expected, actions)
        self.assertTrue(all(event["actor"] == "Administración QA" for event in audit["items"]))


if __name__ == "__main__":
    unittest.main()
