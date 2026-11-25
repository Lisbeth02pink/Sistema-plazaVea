"""Single source of truth for configured inventory status thresholds."""


def inventory_status(stock: int, minimum_stock: int | None, safety_stock: int | None, maximum_stock: int | None,
                     inventory_registered: bool = True) -> str:
    if not inventory_registered:
        return "Pendiente de conteo"
    if maximum_stock is not None and stock > maximum_stock:
        return "Exceso de inventario"
    if minimum_stock is None or safety_stock is None:
        return "Política pendiente"
    if stock <= safety_stock:
        return "Stock crítico"
    if stock <= minimum_stock:
        return "Stock bajo"
    return "Stock normal"
