import {useEffect,useRef,useState} from 'react';
import {AlertTriangle,CalendarDays,CheckSquare,ChevronDown,FileDown,Search,ShieldCheck} from 'lucide-react';
import {api,ApiError,type AuditEvent,type AuditPageResult} from './api';

function localDate(value:Date){return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`}
function initialRange(){const end=new Date();const start=new Date(end);start.setDate(start.getDate()-29);return {from:localDate(start),to:localDate(end)}}
function dateTime(value:string){return new Intl.DateTimeFormat('es-PE',{dateStyle:'short',timeStyle:'medium'}).format(new Date(value))}
const actionNames:Record<string,string>={
  'auth.login':'Inicio de sesión','auth.logout':'Cierre de sesión','auth.login_failed':'Intento de acceso','auth.login_blocked':'Acceso bloqueado','auth.password_reset':'Restableció contraseña',
  'user.created':'Creó usuario','user.updated':'Actualizó usuario','user.deleted':'Eliminó usuario','role.created':'Creó rol','role.updated':'Actualizó rol','role.deleted':'Eliminó rol','role.modules_updated':'Modificó permisos',
  'product.created':'Registró producto','product.updated':'Actualizó producto','product.policy_updated':'Cambió política de inventario',
  'sale.created':'Registró venta','inventory.movement_recorded':'Registró movimiento de inventario',
  'replenishment.request_created':'Creó solicitud de reposición','replenishment.approved':'Aprobó solicitud','replenishment.rejected':'Rechazó solicitud','replenishment.received':'Registró recepción',
  'forecast.model_trained':'Entrenó modelo experimental','forecast.generated':'Generó pronóstico experimental','forecast.local_model_trained':'Entrenó modelo local','forecast.local_generated':'Generó pronóstico local',
  'security.settings_updated':'Actualizó configuración','security.backup_created':'Creó copia de seguridad','security.review_recorded':'Registró revisión','audit.exported':'Exportó bitácora',
};
function actionName(value:string){return actionNames[value]??value.replaceAll('.',' ')}

export default function AuditPage(){
  const range=useRef(initialRange()).current;
  const[search,setSearch]=useState('');
  const[module,setModule]=useState('');
  const[action,setAction]=useState('');
  const[dateFrom,setDateFrom]=useState(range.from);
  const[dateTo,setDateTo]=useState(range.to);
  const[page,setPage]=useState(1);
  const[data,setData]=useState<AuditPageResult|null>(null);
  const[loading,setLoading]=useState(true);
  const[exporting,setExporting]=useState(false);
  const[error,setError]=useState('');
  const requestId=useRef(0);

  function params(){const result=new URLSearchParams();if(search.trim())result.set('q',search.trim());if(module)result.set('module',module);if(action)result.set('action',action);if(dateFrom)result.set('date_from',dateFrom);if(dateTo)result.set('date_to',dateTo);return result}
  useEffect(()=>{
    const id=++requestId.current;
    const timer=window.setTimeout(async()=>{
      setLoading(true);setError('');
      try{const query=params();query.set('page',String(page));query.set('page_size','10');const result=await api.audit(query);if(id===requestId.current)setData(result)}
      catch(err){if(id===requestId.current)setError(err instanceof ApiError?err.message:'No se pudo cargar la bitácora.')}
      finally{if(id===requestId.current)setLoading(false)}
    },search?220:0);
    return()=>window.clearTimeout(timer);
  },[search,module,action,dateFrom,dateTo,page]);

  function resetPage(setter:(value:string)=>void,value:string){setter(value);setPage(1)}
  async function exportRecords(){
    setExporting(true);setError('');
    try{const blob=await api.exportAudit(params());const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`bitacora_${localDate(new Date())}.csv`;document.body.append(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000)}
    catch(err){setError(err instanceof ApiError?err.message:'No se pudo exportar la bitácora.')}
    finally{setExporting(false)}
  }
  const items:AuditEvent[]=data?.items??[];
  const first=data?.total?(page-1)*10+1:0;
  const last=Math.min(page*10,data?.total??0);

  return <div className="content audit-content">
    <div className="title-row"><div><h1>Auditoría y actividades</h1><p>Trazabilidad histórica de las operaciones del sistema</p></div><button className="primary audit-export" onClick={()=>void exportRecords()} disabled={exporting||!data?.total}><FileDown size={15}/>{exporting?'Preparando…':'Exportar registro'}</button></div>
    <div className="audit-notice"><ShieldCheck size={18}/><span><b>Registro de solo consulta</b><small>Los eventos registrados no se pueden editar ni eliminar desde el sistema.</small></span></div>
    {error&&<div className="form-error" role="alert">{error}</div>}
    <div className="audit-filters">
      <label className="search"><Search size={16}/><input value={search} onChange={e=>resetPage(setSearch,e.target.value)} placeholder="Buscar usuario o registro…" aria-label="Buscar usuario o registro"/></label>
      <label className="filter-btn"><CheckSquare size={15}/><select value={module} onChange={e=>resetPage(setModule,e.target.value)} aria-label="Filtrar por módulo"><option value="">Todos los módulos</option>{['Autenticación','Usuarios y roles','Productos','Ventas','Inventarios','Predicción con IA','Alertas','Reposición','Seguridad','Auditoría','Sistema'].map(item=><option key={item}>{item}</option>)}</select><ChevronDown size={13}/></label>
      <label className="filter-btn"><AlertTriangle size={15}/><select value={action} onChange={e=>resetPage(setAction,e.target.value)} aria-label="Filtrar por acción"><option value="">Todas las acciones</option>{(data?.actions??[]).map(item=><option key={item} value={item}>{actionName(item)}</option>)}</select><ChevronDown size={13}/></label>
      <label className="audit-date"><CalendarDays size={15}/><span>Desde</span><input type="date" value={dateFrom} onChange={e=>resetPage(setDateFrom,e.target.value)} aria-label="Fecha inicial"/></label>
      <label className="audit-date"><CalendarDays size={15}/><span>Hasta</span><input type="date" value={dateTo} onChange={e=>resetPage(setDateTo,e.target.value)} aria-label="Fecha final"/></label>
    </div>
    <section className="tablecard audit-tablecard">
      <div className="tablehead"><div><b>Registro de actividades</b><small>{loading?'Actualizando registro…':`${(data?.total??0).toLocaleString('es-PE')} eventos en el período seleccionado`}</small></div><span className="audit-readonly"><i/>Solo consulta</span></div>
      <div className="tablewrap"><table><thead><tr><th>USUARIO</th><th>FECHA Y HORA</th><th>MÓDULO</th><th>ACCIÓN REALIZADA</th><th>REGISTRO RELACIONADO</th><th>RESULTADO</th></tr></thead><tbody>
        {items.map(event=><tr key={event.id}><td>{event.actor}</td><td>{dateTime(event.created_at)}</td><td>{event.module}</td><td>{actionName(event.action)}</td><td className="audit-related">{event.related_record||'—'}</td><td><span className={`audit-result ${event.result==='Exitoso'?'success':'failure'}`}><i/>{event.result}</span></td></tr>)}
      </tbody></table>{!loading&&items.length===0&&<div className="empty">No hay eventos para los filtros seleccionados.</div>}</div>
      <div className="audit-pagination"><small>Mostrando {first}–{last} de {(data?.total??0).toLocaleString('es-PE')} eventos</small><div><button className="filter-btn" disabled={page<=1||loading} onClick={()=>setPage(value=>Math.max(1,value-1))}>Anterior</button><b>{page}</b><button className="filter-btn" disabled={!data||page>=data.pages||loading} onClick={()=>setPage(value=>value+1)}>Siguiente</button></div></div>
    </section>
  </div>
}
