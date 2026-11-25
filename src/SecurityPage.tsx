import {FormEvent,useEffect,useState} from 'react';
import {Archive,CalendarCheck,CheckCircle2,ChevronRight,Download,LockKeyhole,RefreshCw,Save,ShieldCheck,Timer,UsersRound} from 'lucide-react';
import {api,ApiError,type SecurityOverview,type SecuritySettings} from './api';

const initialSettings:SecuritySettings={session_hours:8,failed_attempts:10,lockout_minutes:15,updated_at:''};
function dateTime(value:string|null|undefined){return value?new Intl.DateTimeFormat('es-PE',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'Sin registro'}
function fileSize(bytes:number){return bytes<1024*1024?`${Math.max(1,Math.round(bytes/1024))} KB`:`${(bytes/1024/1024).toFixed(1)} MB`}

export default function SecurityPage({canViewAudit,canViewRoles,onNavigate}:{canViewAudit:boolean;canViewRoles:boolean;onNavigate:(page:string)=>void}){
  const[data,setData]=useState<SecurityOverview|null>(null);
  const[settings,setSettings]=useState(initialSettings);
  const[notes,setNotes]=useState('');
  const[loading,setLoading]=useState(true);
  const[saving,setSaving]=useState(false);
  const[backupBusy,setBackupBusy]=useState(false);
  const[reviewBusy,setReviewBusy]=useState(false);
  const[error,setError]=useState('');
  const[notice,setNotice]=useState('');

  async function refresh(){
    setLoading(true);setError('');
    try{const result=await api.security();setData(result);setSettings(result.settings)}
    catch(err){setError(err instanceof ApiError?err.message:'No se pudo cargar la configuración de seguridad.')}
    finally{setLoading(false)}
  }
  useEffect(()=>{void refresh()},[]);

  async function saveSettings(event:FormEvent){
    event.preventDefault();setSaving(true);setError('');setNotice('');
    try{await api.saveSecuritySettings({session_hours:settings.session_hours,failed_attempts:settings.failed_attempts,lockout_minutes:settings.lockout_minutes});setNotice('Políticas de seguridad guardadas. El cambio de duración se aplica a los próximos inicios de sesión.');await refresh()}
    catch(err){setError(err instanceof ApiError?err.message:'No se pudieron guardar las políticas.')}
    finally{setSaving(false)}
  }
  async function createBackup(){
    setBackupBusy(true);setError('');setNotice('');
    try{await api.createSecurityBackup();setNotice('Copia de seguridad creada.');await refresh()}
    catch(err){setError(err instanceof ApiError?err.message:'No se pudo crear la copia de seguridad.')}
    finally{setBackupBusy(false)}
  }
  async function registerReview(event:FormEvent){
    event.preventDefault();setReviewBusy(true);setError('');setNotice('');
    try{await api.recordSecurityReview(notes);setNotes('');setNotice('Revisión de seguridad registrada.');await refresh()}
    catch(err){setError(err instanceof ApiError?err.message:'No se pudo registrar la revisión.')}
    finally{setReviewBusy(false)}
  }

  const canManage=Boolean(data?.can_manage);
  return <div className="content security-content">
    <div className="title-row"><div><h1>Seguridad y configuración</h1><p>Políticas aplicadas al acceso y actividad del sistema</p></div><button className="filter-btn" onClick={()=>void refresh()} disabled={loading} aria-label="Actualizar seguridad" title="Actualizar"><RefreshCw size={16}/></button></div>
    {error&&<div className="form-error" role="alert">{error}</div>}{notice&&<div className="security-success" role="status"><CheckCircle2 size={16}/>{notice}</div>}
    {loading&&!data?<div className="empty">Cargando configuración…</div>:data&&<>
      {!canManage&&<div className="security-readonly"><ShieldCheck size={16}/>Vista de solo lectura. Solo Super Administrador puede modificar políticas, copias y revisiones.</div>}
      <div className="security-grid security-operations">
        {canViewRoles?<button className="security-card security-card-link" onClick={()=>onNavigate('Usuarios y roles')}><span className="security-icon green"><UsersRound size={19}/></span><span className="security-copy"><small>ROLES Y PERMISOS</small><b>Administrar roles</b><em>Gestiona permisos por módulo.</em></span><ChevronRight size={17} className="security-arrow" aria-hidden="true"/></button>:<article className="security-card"><span className="security-icon green"><UsersRound size={19}/></span><span className="security-copy"><small>ROLES Y PERMISOS</small><b>Acceso a módulos</b><em>Controlado por permisos asignados a cada rol.</em></span><button className="security-arrow-button" disabled aria-label="Roles y permisos, acceso no asignado" title="Tu rol no tiene acceso a Usuarios y roles"><ChevronRight size={17}/></button></article>}
        {canViewAudit?<button className="security-card security-card-link" onClick={()=>onNavigate('Auditoría')}><span className="security-icon blue"><ShieldCheck size={19}/></span><span className="security-copy"><small>BITÁCORA DEL SISTEMA</small><b>{data.events.length} eventos recientes</b><em>Consulta la trazabilidad completa en Auditoría.</em></span><ChevronRight size={17} className="security-arrow" aria-hidden="true"/></button>:<article className="security-card"><span className="security-icon blue"><ShieldCheck size={19}/></span><span className="security-copy"><small>BITÁCORA DEL SISTEMA</small><b>{data.events.length} eventos recientes</b><em>Consulta la trazabilidad completa en Auditoría.</em></span><button className="security-arrow-button" disabled aria-label="Auditoría, acceso no asignado" title="Tu rol no tiene acceso a Auditoría"><ChevronRight size={17}/></button></article>}
        <article className="security-card"><span className="security-icon amber"><Archive size={19}/></span><span className="security-copy"><small>COPIAS DE SEGURIDAD</small><b>{canManage?`${data.backups.length} copias disponibles`:'Administradas por Super Administrador'}</b><em>{data.backups[0]?`Última copia: ${dateTime(data.backups[0].created_at)}`:'No se ha creado una copia.'}</em></span>{canManage&&<button className="security-action" onClick={()=>void createBackup()} disabled={backupBusy}>{backupBusy?'Creando…':'Crear copia'}</button>}<button className="security-arrow-button" onClick={()=>document.getElementById('security-backups')?.scrollIntoView({behavior:'smooth',block:'center'})} disabled={!canManage} aria-label="Ver copias de seguridad" title={canManage?'Ver copias de seguridad':'Solo Super Administrador'}><ChevronRight size={17}/></button></article>
        <article className="security-card"><span className="security-icon blue"><CalendarCheck size={19}/></span><span className="security-copy"><small>REVISIÓN DE SEGURIDAD</small><b>{data.latest_review?'Revisión registrada':'Sin revisión registrada'}</b><em>{data.latest_review?`${dateTime(data.latest_review.reviewed_at)} · ${data.latest_review.reviewer}`:'Registra una revisión cuando completes la comprobación.'}</em></span>{canManage&&<span className="security-badge blue"><i/>{data.latest_review?'REGISTRADA':'PENDIENTE'}</span>}<button className="security-arrow-button" onClick={()=>document.getElementById('security-review')?.scrollIntoView({behavior:'smooth',block:'center'})} disabled={!canManage} aria-label="Ver revisión de seguridad" title={canManage?'Ver revisión de seguridad':'Solo Super Administrador'}><ChevronRight size={17}/></button></article>
      </div>

      <form className="tablecard security-policy-card" onSubmit={saveSettings}>
        <div className="tablehead"><div><b>Políticas de acceso</b><small>Estos valores se aplican al iniciar sesión</small></div>{canManage&&<button className="primary" disabled={saving}><Save size={15}/>{saving?'Guardando…':'Guardar cambios'}</button>}</div>
        <div className="security-controls">
          <label className="security-field"><span><Timer size={17}/><b>Duración de sesión</b></span><div><input type="number" min={1} max={24} step={1} value={settings.session_hours} disabled={!canManage||saving} onChange={e=>setSettings({...settings,session_hours:Number(e.target.value)})}/><small>horas</small></div><em>Se aplica a las nuevas sesiones.</em></label>
          <label className="security-field"><span><LockKeyhole size={17}/><b>Intentos fallidos antes del bloqueo</b></span><div><input type="number" min={3} max={20} step={1} value={settings.failed_attempts} disabled={!canManage||saving} onChange={e=>setSettings({...settings,failed_attempts:Number(e.target.value)})}/><small>intentos</small></div><em>Se cuentan dentro del período de bloqueo.</em></label>
          <label className="security-field"><span><Timer size={17}/><b>Período de bloqueo</b></span><div><input type="number" min={1} max={60} step={1} value={settings.lockout_minutes} disabled={!canManage||saving} onChange={e=>setSettings({...settings,lockout_minutes:Number(e.target.value)})}/><small>minutos</small></div><em>Después de alcanzar el límite de intentos.</em></label>
        </div>
        <div className="security-updated">Último cambio: {dateTime(data.settings.updated_at)}</div>
      </form>

      {canManage&&<section id="security-backups" className="tablecard security-backups"><div className="tablehead"><div><b>Copias de seguridad</b><small>Descarga copias de la base de datos local</small></div></div><div className="security-backup-list">{data.backups.length?data.backups.map(item=><div className="security-backup-row" key={item.filename}><Archive size={16}/><span><b>{item.filename}</b><small>{dateTime(item.created_at)} · {fileSize(item.size_bytes)}</small></span><a className="filter-btn" href={`/api/admin/security/backups/${encodeURIComponent(item.filename)}`} download aria-label={`Descargar ${item.filename}`}><Download size={15}/></a></div>):<div className="empty">Aún no hay copias de seguridad.</div>}</div></section>}

      {canManage&&<form id="security-review" className="tablecard security-review-form" onSubmit={registerReview}><div className="tablehead"><div><b>Registrar revisión</b><small>Guarda la fecha, responsable y nota de la revisión</small></div></div><label>Notas de la revisión (opcional)<textarea maxLength={500} rows={4} value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Resumen de las comprobaciones realizadas"/></label><button className="primary" disabled={reviewBusy}><CalendarCheck size={15}/>{reviewBusy?'Guardando…':'Registrar revisión'}</button></form>}
    </>}
  </div>
}
