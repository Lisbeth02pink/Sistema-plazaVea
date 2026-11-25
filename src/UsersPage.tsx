import {FormEvent,useEffect,useMemo,useState} from 'react';
import {ChevronDown,Filter,Pencil,Plus,RefreshCw,Search,Shield,ShieldCheck,Trash2,UserRoundPlus,UsersRound,X} from 'lucide-react';
import {api,ApiError,type ManagedRole,type ManagedUser} from './api';

const emptyForm={name:'',email:'',password:'',role:'Personal de operaciones',is_active:true};
const moduleCatalog=[['dashboard','Dashboard'],['products','Productos'],['sales','Ventas históricas'],['inventory','Inventarios'],['forecast','Predicción con IA'],['alerts','Alertas'],['replenishment','Reposición'],['users','Usuarios y roles'],['audit','Auditoría'],['security','Seguridad']] as const;
type DeleteTarget={kind:'user';value:ManagedUser}|{kind:'role';value:ManagedRole};
function initials(name:string){return name.trim().split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'US'}
function lastAccess(value:string|null){return value?new Intl.DateTimeFormat('es-PE',{dateStyle:'short',timeStyle:'short'}).format(new Date(value)):'Sin acceso'}

export default function UsersPage({currentUserId,currentRole,currentModules,onCurrentUserUpdated}:{currentUserId:number;currentRole:string;currentModules:string[];onCurrentUserUpdated:()=>Promise<void>}){
  const[tab,setTab]=useState<'Usuarios'|'Roles y permisos'>('Usuarios');
  const[search,setSearch]=useState('');
  const[role,setRole]=useState('Todos los roles');
  const[users,setUsers]=useState<ManagedUser[]>([]);
  const[roles,setRoles]=useState<ManagedRole[]>([]);
  const[loading,setLoading]=useState(true);
  const[error,setError]=useState('');
  const[notice,setNotice]=useState('');
  const[editing,setEditing]=useState<ManagedUser|null>(null);
  const[roleDialog,setRoleDialog]=useState(false);
  const[editingRole,setEditingRole]=useState<ManagedRole|null>(null);
  const[roleForm,setRoleForm]=useState({name:'',description:''});
  const[roleFormError,setRoleFormError]=useState('');
  const[roleSaving,setRoleSaving]=useState(false);
  const[deleteTarget,setDeleteTarget]=useState<DeleteTarget|null>(null);
  const[deleteError,setDeleteError]=useState('');
  const[deleting,setDeleting]=useState(false);
  const[permissionRole,setPermissionRole]=useState<ManagedRole|null>(null);
  const[permissionModules,setPermissionModules]=useState<string[]>([]);
  const[permissionSaving,setPermissionSaving]=useState(false);
  const[permissionError,setPermissionError]=useState('');
  const[dialog,setDialog]=useState(false);
  const[saving,setSaving]=useState(false);
  const[form,setForm]=useState(emptyForm);
  const[formError,setFormError]=useState('');
  const[page,setPage]=useState(1);
  const pageSize=6;

  async function refresh(){
    setLoading(true);setError('');
    try{const [userRows,roleRows]=await Promise.all([api.users(),api.roles()]);setUsers(userRows);setRoles(roleRows)}
    catch(err){setError(err instanceof ApiError&&err.status===403?'Tu cuenta no tiene permiso para administrar usuarios.':err instanceof ApiError?err.message:'No se pudo cargar la gestión de usuarios.')}
    finally{setLoading(false)}
  }
  useEffect(()=>{void refresh()},[]);

  const filtered=useMemo(()=>users.filter(u=>(u.name.toLowerCase().includes(search.toLowerCase())||u.email.toLowerCase().includes(search.toLowerCase()))&&(role==='Todos los roles'||u.role===role)),[users,search,role]);
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize));
  const visible=filtered.slice((page-1)*pageSize,page*pageSize);
  const editingSelf=editing?.id===currentUserId;
  const selfRoleOptions=roles.filter(r=>currentModules.every(module=>r.can_manage_users||r.modules.includes(module)));
  useEffect(()=>setPage(1),[search,role]);

  function openCreate(){setEditing(null);setForm({...emptyForm,role:roles.find(r=>r.name==='Personal de operaciones')?.name??roles[0]?.name??''});setFormError('');setDialog(true)}
  function openEdit(user:ManagedUser){setEditing(user);setForm({name:user.name,email:user.email,password:'',role:user.role,is_active:Boolean(user.is_active)});setFormError('');setDialog(true)}
  async function save(event:FormEvent){
    event.preventDefault();setFormError('');
    if(form.password&&form.password.length<8){setFormError('La contraseña debe tener al menos 8 caracteres.');return}
    if(!editing&&form.password.length<8){setFormError('La contraseña inicial debe tener al menos 8 caracteres.');return}
    setSaving(true);
    try{
      if(editing){
        const result=await api.updateUser(editing.id,{name:form.name,email:form.email,role:form.role,is_active:form.is_active,...(form.password?{password:form.password}:{})});
        if(result.session_revoked){setDialog(false);setNotice('Tu cuenta fue modificada y la sesión se cerró. Inicia sesión de nuevo.');window.setTimeout(()=>location.assign('/login'),900);return}
        if(editing.id===currentUserId&&form.role!==currentRole)await onCurrentUserUpdated();
        setNotice('Los datos del usuario se guardaron.');
      }else{
        await api.createUser({name:form.name,email:form.email,password:form.password,role:form.role});
        setNotice('Usuario creado. Ya puede iniciar sesión con el correo y la contraseña asignados.');
      }
      setDialog(false);await refresh();
    }catch(err){setFormError(err instanceof ApiError?err.message:'No se pudo guardar el usuario.')}
    finally{setSaving(false)}
  }
  function openNewRole(){setEditingRole(null);setRoleForm({name:'',description:''});setRoleFormError('');setRoleDialog(true)}
  function openRoleEdit(item:ManagedRole){setEditingRole(item);setRoleForm({name:item.name,description:item.description});setRoleFormError('');setRoleDialog(true)}
  async function saveRole(event:FormEvent){
    event.preventDefault();setRoleFormError('');setRoleSaving(true);
    try{
      if(editingRole){await api.updateRole(editingRole.name,roleForm);if(editingRole.name===currentRole&&roleForm.name!==currentRole)await onCurrentUserUpdated();setNotice('Rol actualizado.')}
      else{await api.createRole({...roleForm,modules:[]});setNotice('Rol creado. Configura sus módulos de acceso.')}
      setRoleDialog(false);await refresh();
    }catch(err){setRoleFormError(err instanceof ApiError?err.message:'No se pudo guardar el rol.')}
    finally{setRoleSaving(false)}
  }
  async function confirmDelete(){
    if(!deleteTarget)return;
    setDeleting(true);setDeleteError('');
    try{
      if(deleteTarget.kind==='user'){await api.deleteUser(deleteTarget.value.id);if(editing?.id===deleteTarget.value.id)setDialog(false);setNotice('Usuario eliminado.')}
      else{await api.deleteRole(deleteTarget.value.name);setNotice('Rol eliminado.')}
      setDeleteTarget(null);await refresh();
    }catch(err){setDeleteError(err instanceof ApiError?err.message:'No se pudo eliminar.')}
    finally{setDeleting(false)}
  }
  function openPermissions(item:ManagedRole){setPermissionRole(item);setPermissionModules(item.modules);setPermissionError('')}
  async function savePermissions(){
    if(!permissionRole)return;
    setPermissionSaving(true);setPermissionError('');
    try{await api.updateRoleModules(permissionRole.name,permissionModules);setPermissionRole(null);setNotice(`Permisos por módulo guardados para ${permissionRole.name}.`);await refresh()}
    catch(err){setPermissionError(err instanceof ApiError?err.message:'No se pudieron guardar los permisos.')}
    finally{setPermissionSaving(false)}
  }
  return <div className="content admin-content">
    <div className="title-row"><div><h1>Usuarios, roles y permisos</h1><p>Administra cuentas y permisos de acceso por módulo</p></div><button className="primary" onClick={tab==='Usuarios'?openCreate:openNewRole}>{tab==='Usuarios'?<><UserRoundPlus size={16}/>Agregar usuario</>:<><Plus size={16}/>Nuevo rol</>}</button></div>
    <div className="admin-tabs">{(['Usuarios','Roles y permisos'] as const).map(t=><button key={t} className={`admin-tab ${tab===t?'selected':''}`} onClick={()=>setTab(t)}>{t}</button>)}</div>
    {tab==='Usuarios'?<>
      <div className="filters admin-filters"><label className="search"><Search size={16}/><input aria-label="Buscar usuario o correo" placeholder="Buscar usuario o correo..." value={search} onChange={e=>setSearch(e.target.value)}/></label><label className="filter-btn"><Filter size={16}/><select aria-label="Filtrar por rol" value={role} onChange={e=>setRole(e.target.value)}><option>Todos los roles</option>{roles.map(r=><option key={r.name}>{r.name}</option>)}</select><ChevronDown size={14}/></label></div>
      <section className="tablecard"><div className="tablehead"><div><b>Usuarios del sistema</b><small>Administración de cuentas y accesos</small></div><button className="filter-btn refresh-button" onClick={()=>void refresh()} disabled={loading} aria-label="Actualizar usuarios" title="Actualizar usuarios"><RefreshCw size={16} className={loading?'spinning':''}/></button></div>
        {error?<div className="empty form-error" role="alert">{error}</div>:loading?<div className="empty">Cargando usuarios…</div>:<><div className="tablewrap"><table className="user-table"><thead><tr><th>USUARIO</th><th>CORREO INSTITUCIONAL</th><th>ROL ASIGNADO</th><th>ESTADO</th><th>ÚLTIMO ACCESO</th><th>ACCIONES</th></tr></thead><tbody>{visible.map(u=><tr key={u.id}><td><div className="user-cell"><span className="user-avatar">{initials(u.name)}</span><b>{u.name}</b></div></td><td>{u.email}</td><td><span className={`role-pill ${u.role==='Administrador'?'role-admin':u.role==='Responsable de abastecimiento'?'role-supply':'role-ops'}`}><i/>{u.role}</span></td><td><span className={`user-state ${u.is_active?'is-active':'is-suspended'}`}><i/>{u.is_active?'Activo':'Suspendido'}</span></td><td>{lastAccess(u.last_login_at)}</td><td><div className="table-actions"><button className="table-action" aria-label={`Editar ${u.name}`} title="Editar usuario" onClick={()=>openEdit(u)}><Pencil size={15}/></button><button className="table-action danger" aria-label={u.id===currentUserId?'No puedes eliminar tu propia cuenta':`Eliminar ${u.name}`} title={u.id===currentUserId?'No puedes eliminar tu propia cuenta':'Eliminar usuario'} disabled={u.id===currentUserId} onClick={()=>{setDeleteTarget({kind:'user',value:u});setDeleteError('')}}><Trash2 size={15}/></button></div></td></tr>)}</tbody></table>{visible.length===0&&<div className="empty">No se encontraron usuarios.</div>}</div><div className="pagination"><small>Mostrando {filtered.length?`${(page-1)*pageSize+1}–${Math.min(page*pageSize,filtered.length)}`:'0'} de {filtered.length} usuarios</small><div><button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Anterior</button><b>{page}</b><button disabled={page>=pages} onClick={()=>setPage(p=>p+1)}>Siguiente</button></div></div></>}
      </section>
    </>:<section className="tablecard roles-card"><div className="tablehead"><div><b>Roles del sistema</b><small>{roles.length} roles disponibles · Permisos configurados por módulo</small></div></div><div className="roles-list">{roles.map(r=>{const builtInAdmin=r.name==='Administrador';const systemProtected=Boolean(r.can_manage_users)||builtInAdmin;const showProtected=systemProtected&&!(builtInAdmin&&currentRole==='Super Administrador');const canEditRole=!r.can_manage_users&&(!builtInAdmin||currentRole==='Super Administrador');const canDeleteRole=r.user_count===0&&(!systemProtected||(builtInAdmin&&currentRole==='Super Administrador'));return <article className="role-row" key={r.name}><span className="role-mark"><UsersRound size={18}/></span><div className="role-info"><b>{r.name}{showProtected&&<em className="protected-role">Protegido</em>}</b><small>{r.description}</small></div><span className="role-pending"><i/>{r.user_count} usuarios</span><button className={`role-permissions-button ${r.modules.length?'has-permission':''}`} onClick={()=>openPermissions(r)} disabled={Boolean(r.can_manage_users)||(builtInAdmin&&currentRole!=='Super Administrador')} aria-label={`Configurar permisos de ${r.name}`}>{r.can_manage_users?<ShieldCheck size={15}/>:<Shield size={15}/>}<span>{r.modules.length} módulos</span></button>{canEditRole&&<button className="role-action" aria-label={`Editar rol ${r.name}`} title="Editar rol" onClick={()=>openRoleEdit(r)}><Pencil size={15}/></button>}{(!systemProtected||(builtInAdmin&&currentRole==='Super Administrador'))&&<button className="role-action danger" aria-label={r.user_count?`No puedes eliminar el rol ${r.name} mientras tenga usuarios`:`Eliminar rol ${r.name}`} title={r.user_count?'Reasigna sus usuarios antes de eliminar este rol':'Eliminar rol'} disabled={!canDeleteRole} onClick={()=>{setDeleteTarget({kind:'role',value:r});setDeleteError('')}}><Trash2 size={15}/></button>}</article>})}</div></section>}
    {dialog&&<div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!saving)setDialog(false)}}><form className="modal admin-modal" onSubmit={save}><div className="modal-head"><div><b>{editing?'Editar usuario':'Agregar usuario'}</b><small>{editing?'Actualiza sus datos, rol o estado':'Crea una cuenta para el personal autorizado'}</small></div><button type="button" onClick={()=>setDialog(false)} aria-label="Cerrar" disabled={saving}><X size={18}/></button></div>
      <label>Nombre completo<input required maxLength={180} autoComplete="name" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
      <label>Correo institucional<input required type="email" maxLength={254} autoComplete="email" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/></label>
      <label>{editing?'Nueva contraseña (opcional)':'Contraseña inicial'}<input type="password" autoComplete={editing?'new-password':'new-password'} minLength={editing?0:8} required={!editing} placeholder="Mínimo 8 caracteres" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/></label>
      <label>Rol asignado{editingSelf&&<span className="protected-field"> · conserva todos tus permisos</span>}<select required value={form.role} disabled={!roles.length||(editingSelf&&selfRoleOptions.every(r=>r.name===currentRole))} onChange={e=>setForm({...form,role:e.target.value})}>{(editingSelf?selfRoleOptions:roles).map(r=><option key={r.name} value={r.name}>{r.name}</option>)}</select></label>
      {editing&&<label className="user-active-toggle"><input type="checkbox" checked={form.is_active} disabled={editing.id===currentUserId} onChange={e=>setForm({...form,is_active:e.target.checked})}/><span>Cuenta activa {editing.id===currentUserId&&' (no puedes suspender tu propia cuenta)'}</span></label>}
      {formError&&<div className="form-error" role="alert">{formError}</div>}
      <div className="modal-actions user-modal-actions">{editing&&<button type="button" className="danger-text-btn" disabled={saving||editing.id===currentUserId} onClick={()=>{setDeleteTarget({kind:'user',value:editing});setDeleteError('')}}><Trash2 size={14}/>{editing.id===currentUserId?'No puedes eliminarte':'Eliminar usuario'}</button>}<span/><button type="button" className="filter-btn" disabled={saving} onClick={()=>setDialog(false)}>Cancelar</button><button className="primary" disabled={saving||!roles.length}>{saving?'Guardando…':<><Plus size={15}/>{editing?'Guardar cambios':'Crear usuario'}</>}</button></div>
    </form></div>}
    {roleDialog&&<div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!roleSaving)setRoleDialog(false)}}><form className="modal admin-modal" onSubmit={saveRole}><div className="modal-head"><div><b>{editingRole?'Editar rol':'Nuevo rol'}</b><small>{editingRole?'Actualiza el nombre o descripción':'Crea un rol y después configura sus módulos'}</small></div><button type="button" onClick={()=>setRoleDialog(false)} aria-label="Cerrar" disabled={roleSaving}><X size={18}/></button></div><label>Nombre del rol<input required minLength={2} maxLength={80} value={roleForm.name} onChange={e=>setRoleForm({...roleForm,name:e.target.value})} placeholder="Ej. Supervisor de tienda" disabled={editingRole?.name==='Administrador'}/></label><label>Descripción (opcional)<input maxLength={180} value={roleForm.description} onChange={e=>setRoleForm({...roleForm,description:e.target.value})} placeholder="Responsabilidades del rol"/></label><div className="role-create-note"><Shield size={16}/>{editingRole?.name==='Administrador'?'Puedes editar la descripción y los módulos del rol; su nombre es fijo.':editingRole?'Los módulos asignados se conservan al editar el rol.':'El rol nuevo inicia sin módulos. Puedes asignarlos desde “Permisos por módulo”.'}</div>{roleFormError&&<div className="form-error" role="alert">{roleFormError}</div>}<div className="modal-actions"><button type="button" className="filter-btn" disabled={roleSaving} onClick={()=>setRoleDialog(false)}>Cancelar</button><button className="primary" disabled={roleSaving}>{roleSaving?'Guardando…':editingRole?'Guardar cambios':'Crear rol'}</button></div></form></div>}
    {permissionRole&&(()=>{const builtInAdmin=permissionRole.name==='Administrador';const systemProtected=Boolean(permissionRole.can_manage_users)||(builtInAdmin&&currentRole!=='Super Administrador');const editingAdminRole=builtInAdmin&&currentRole==='Super Administrador';const editingOwnSuperRole=permissionRole.name===currentRole&&currentRole==='Super Administrador';const protectedRole=systemProtected||(permissionRole.name===currentRole&&!editingOwnSuperRole);return <div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!permissionSaving)setPermissionRole(null)}}><section className="modal role-permissions-modal"><div className="modal-head"><div><small className="role-modal-eyebrow">PERMISOS POR MÓDULO</small><b>{permissionRole.name}</b><small>{permissionRole.description}</small></div><button onClick={()=>setPermissionRole(null)} aria-label="Cerrar" disabled={permissionSaving}><X size={18}/></button></div>{systemProtected&&<div className="admin-all-modules"><ShieldCheck size={18}/><span><b>Rol del sistema protegido</b><small>Solo el Super Administrador puede editar los módulos del rol Administrador.</small></span></div>}{editingAdminRole&&<div className="admin-all-modules"><ShieldCheck size={18}/><span><b>Configuración del rol Administrador</b><small>Puedes seleccionar los módulos disponibles para las cuentas con este rol. El nombre del rol se mantiene fijo.</small></span></div>}{editingOwnSuperRole&&<div className="admin-all-modules"><ShieldCheck size={18}/><span><b>Rol Super Administrador</b><small>Puedes configurar tus módulos, pero Usuarios y roles permanece activo para conservar la administración del sistema.</small></span></div>}{protectedRole&&!systemProtected&&<div className="admin-all-modules"><ShieldCheck size={18}/><span><b>Rol de tu cuenta protegido</b><small>No puedes modificar los módulos asignados a tu propio rol.</small></span></div>}<div className="module-permission-list">{moduleCatalog.map(([key,label])=><label className="module-permission" key={key}><input type="checkbox" checked={Boolean(permissionRole.can_manage_users)||permissionModules.includes(key)} disabled={protectedRole||(editingOwnSuperRole&&key==='users')} onChange={e=>setPermissionModules(current=>e.target.checked?[...current,key]:current.filter(item=>item!==key))}/><span><b>{label}</b><small>Acceso al módulo {label}</small></span></label>)}</div>{permissionError&&<div className="form-error" role="alert">{permissionError}</div>}<div className="modal-actions"><button className="filter-btn" disabled={permissionSaving} onClick={()=>setPermissionRole(null)}>Cerrar</button>{protectedRole?<button className="primary" onClick={()=>setPermissionRole(null)}>Listo</button>:<button className="primary" disabled={permissionSaving} onClick={()=>void savePermissions()}>{permissionSaving?'Guardando…':'Guardar permisos'}</button>}</div></section></div>})()}
    {deleteTarget&&<div className="overlay delete-overlay"><section className="modal delete-modal"><div className="modal-head"><div><b>¿Eliminar {deleteTarget.kind==='user'?'usuario':'rol'}?</b><small>{deleteTarget.kind==='user'?deleteTarget.value.name:deleteTarget.value.name}</small></div><button onClick={()=>{setDeleteTarget(null);setDeleteError('')}} aria-label="Cerrar" disabled={deleting}><X size={18}/></button></div><p>{deleteTarget.kind==='user'?'La cuenta y sus sesiones activas se eliminarán.':'El rol no podrá eliminarse mientras tenga usuarios asignados.'} Esta acción no se puede deshacer.</p>{deleteError&&<div className="form-error" role="alert">{deleteError}</div>}<div className="modal-actions"><button className="filter-btn" disabled={deleting} onClick={()=>setDeleteTarget(null)}>Cancelar</button><button className="danger-solid-btn" disabled={deleting} onClick={()=>void confirmDelete()}><Trash2 size={14}/>{deleting?'Eliminando…':'Eliminar'}</button></div></section></div>}
    {notice&&<button className="toast" onClick={()=>setNotice('')}>{notice}<X size={15}/></button>}
  </div>
}
