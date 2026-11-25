import {useEffect,useState} from 'react';
import {AlertTriangle,Box,CalendarDays,ChevronDown,ClipboardList,Layers,Package,TrendingUp,Bell} from 'lucide-react';
import {Area,AreaChart,CartesianGrid,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {api,type DashboardData} from './api';
import './dashboard.css';

const dateValue=(date:Date)=>{const local=new Date(date.getTime()-date.getTimezoneOffset()*60000);return local.toISOString().slice(0,10)};
const money=(value:number)=>`S/ ${value.toLocaleString('es-PE',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const units=(value:number)=>value.toLocaleString('es-PE');

export default function DashboardPage({onNavigate}:{onNavigate:(page:string)=>void}){
  const today=dateValue(new Date());
  const initialStart=dateValue(new Date(Date.now()-29*86400000));
  const[fromDate,setFromDate]=useState(initialStart),[toDate,setToDate]=useState(today),[category,setCategory]=useState(''),[productId,setProductId]=useState('');
  const[data,setData]=useState<DashboardData|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const productOptions=data?.product_options??[];
  const visibleProducts=category?productOptions.filter(p=>p.category===category):productOptions;
  useEffect(()=>{if(productId&&productOptions.length&&!productOptions.some(p=>String(p.id)===productId))setProductId('')},[productId,productOptions]);
  useEffect(()=>{
    let active=true;
    if(fromDate>toDate){setError('La fecha inicial debe ser anterior o igual a la fecha final.');setLoading(false);return}
    setLoading(true);setError('');
    const params=new URLSearchParams({from_date:fromDate,to_date:toDate});
    if(category)params.set('category',category);if(productId)params.set('product_id',productId);
    api.dashboard(params).then(result=>{if(active)setData(result)}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:'No se pudo cargar el dashboard.')}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[fromDate,toDate,category,productId]);

  const summary=data?.summary;
  const metrics=[
    {title:'Ventas del período',value:summary?money(summary.total_sales):'—',detail:summary?`${units(summary.transactions)} transacciones`:'Importe registrado',icon:TrendingUp,color:'neutral',go:'Ventas históricas'},
    {title:'Unidades vendidas',value:summary?units(summary.units_sold):'—',detail:'Según ventas registradas',icon:Box,color:'green',go:'Ventas históricas'},
    {title:'Stock registrado',value:summary?units(summary.stock_units):'—',detail:`Suma de cantidades · ${summary?.products??0} productos`,icon:Package,color:'blue',go:'Inventarios'},
    {title:'Stock crítico',value:summary?units(summary.stock_critical):'—',detail:`${summary?.stock_low??0} con stock bajo`,icon:AlertTriangle,color:'red',go:'Inventarios'},
    {title:'Exceso de inventario',value:summary?units(summary.stock_excess):'—',detail:'Productos sobre su máximo',icon:Layers,color:'blue',go:'Inventarios'},
    {title:'Alertas activas',value:summary?units(summary.active_alerts):'—',detail:'Reglas vigentes',icon:Bell,color:'amber',go:'Alertas'},
    {title:'Reposiciones pendientes',value:summary?units(summary.pending_replenishments):'—',detail:'En revisión o por recibir',icon:ClipboardList,color:'red',go:'Reposición'},
  ];
  const saleChart=data?.sales_daily.map(point=>({...point,label:new Date(`${point.day}T12:00:00`).toLocaleDateString('es-PE',{day:'2-digit',month:'short'})}))??[];
  const forecastChart=data?.forecast.daily.map(point=>({...point,label:new Date(`${point.day}T12:00:00`).toLocaleDateString('es-PE',{day:'2-digit',month:'short'})}))??[];
  const setPeriod=(days:number)=>{setFromDate(dateValue(new Date(Date.now()-(days-1)*86400000)));setToDate(today)};

  return <div className="content dashboard-page">
    <div className="title-row"><div><h1>Resumen ejecutivo</h1><p>Indicadores de ventas, inventario y reposición</p></div><span className="dash-period"><CalendarDays size={15}/>{data?`${data.period.from_date} — ${data.period.to_date}`:'Período seleccionado'}</span></div>
    <div className="dashboard-filters">
      <label className="filter-btn"><CalendarDays size={15}/><span>Desde</span><input type="date" value={fromDate} max={toDate} onChange={e=>setFromDate(e.target.value)}/></label>
      <label className="filter-btn"><span>Hasta</span><input type="date" value={toDate} min={fromDate} max={today} onChange={e=>setToDate(e.target.value)}/></label>
      <label className="filter-btn"><span>Categoría</span><select value={category} onChange={e=>{setCategory(e.target.value);setProductId('')}}><option value="">Todas</option>{(data?.categories??[]).map(item=><option key={item} value={item}>{item}</option>)}</select><ChevronDown size={13}/></label>
      <label className="filter-btn"><span>Producto</span><select value={productId} onChange={e=>setProductId(e.target.value)}><option value="">Todos</option>{visibleProducts.map(product=><option key={product.id} value={product.id}>{product.name}</option>)}</select><ChevronDown size={13}/></label>
      <div className="dash-presets"><button onClick={()=>setPeriod(7)}>7 días</button><button className={fromDate===initialStart&&toDate===today?'selected':''} onClick={()=>setPeriod(30)}>30 días</button><button onClick={()=>setPeriod(90)}>90 días</button></div>
    </div>
    {error&&<div className="dashboard-error" role="alert">{error}</div>}
    {summary&&summary.stock_policy_missing>0&&<div className="dashboard-note"><AlertTriangle size={15}/>{summary.stock_policy_missing} productos del filtro todavía no tienen políticas completas de inventario. Sus estados no se clasifican hasta configurarlas.</div>}
    <div className="metrics dashboard-metrics">{metrics.map(({title,value,detail,icon:Icon,color,go})=><button className="metric" key={title} onClick={()=>onNavigate(go)} disabled={loading}><div className="metric-top"><span>{title}</span><i className={color}><Icon size={18}/></i></div><strong>{loading?'…':value}</strong><small>{detail}</small></button>)}</div>
    <div className="dashboard-grid">
      <section className="chart-card dashboard-chart"><div className="card-title"><div><b>Ventas históricas</b><small>Importe diario de ventas registradas en soles</small></div></div>
        {error?<div className="dash-empty"><b>No se pudieron consultar las ventas</b><span>Revisa la conexión e inténtalo de nuevo.</span></div>:loading?<div className="dash-empty">Cargando ventas…</div>:saleChart.length===0?<div className="dash-empty"><TrendingUp size={22}/><b>Sin ventas en este período</b><span>Cuando registres ventas, aquí aparecerán los importes diarios.</span></div>:<div className="dash-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={saleChart} margin={{top:15,right:12,left:4,bottom:0}}><defs><linearGradient id="dashboardSalesFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#df1522" stopOpacity={.14}/><stop offset="100%" stopColor="#df1522" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#edf0f3" vertical={false}/><XAxis dataKey="label" tickLine={false} axisLine={false} tick={{fontSize:10,fill:'#8792a2'}} minTickGap={28}/><YAxis tickLine={false} axisLine={false} tick={{fontSize:10,fill:'#8792a2'}} tickFormatter={v=>`${Math.round(Number(v))}`} width={44}/><Tooltip formatter={value=>money(Number(value))}/><Area type="monotone" dataKey="sales" name="Ventas" stroke="#df1522" strokeWidth={2.2} fill="url(#dashboardSalesFill)"/></AreaChart></ResponsiveContainer></div>}
      </section>
      <section className="chart-card forecast-card"><div className="card-title"><div><b>Pronóstico de demanda</b><small>Estimación del modelo local</small></div><span className="forecast-tag">Estimación IA</span></div>
        {error?<div className="dash-empty"><b>No se pudo consultar el pronóstico</b><span>Revisa la conexión e inténtalo de nuevo.</span></div>:loading?<div className="dash-empty">Cargando pronóstico…</div>:forecastChart.length===0?<div className="dash-empty"><TrendingUp size={22}/><b>Pronóstico no disponible</b><span>Se mostrará cuando haya un modelo local validado y vigente para los productos filtrados.</span></div>:<><div className="dash-forecast-total">{units(forecastChart.reduce((sum,item)=>sum+item.units,0))}<small>unidades estimadas · {data?.forecast.products_count} productos</small></div><div className="dash-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={forecastChart} margin={{top:15,right:12,left:4,bottom:0}}><defs><linearGradient id="dashboardForecastFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#2866c7" stopOpacity={.12}/><stop offset="100%" stopColor="#2866c7" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#edf0f3" vertical={false}/><XAxis dataKey="label" tickLine={false} axisLine={false} tick={{fontSize:10,fill:'#8792a2'}} minTickGap={28}/><YAxis tickLine={false} axisLine={false} tick={{fontSize:10,fill:'#8792a2'}} width={35}/><Tooltip formatter={value=>`${units(Number(value))} un.`}/><Area type="monotone" dataKey="units" name="Demanda estimada" stroke="#2866c7" strokeWidth={2.2} fill="url(#dashboardForecastFill)"/></AreaChart></ResponsiveContainer></div></>}
      </section>
      <section className="tablecard dash-list-card"><div className="tablehead"><div><b>Productos más vendidos</b><small>Por unidades en el período seleccionado</small></div><button onClick={()=>onNavigate('Ventas históricas')}>Ver ventas</button></div>{error?<div className="dash-empty short">No se pudieron consultar las ventas.</div>:!data?.top_products.length?<div className="dash-empty short">Todavía no hay productos vendidos en este período.</div>:<div className="dash-ranked">{data.top_products.map((product,index)=><div key={product.product_id}><span className="rank">{index+1}</span><span className="rank-name"><b>{product.name}</b><small>{product.code} · {product.category}</small></span><span className="rank-value"><b>{units(product.units)} un.</b><small>{money(product.sales)}</small></span></div>)}</div>}</section>
      <section className="tablecard dash-list-card"><div className="tablehead"><div><b>Alertas activas</b><small>Prioridad y estado actual</small></div><button onClick={()=>onNavigate('Alertas')}>Ver todas</button></div>{error?<div className="dash-empty short">No se pudieron consultar las alertas.</div>:!data?.recent_alerts.length?<div className="dash-empty short">No hay alertas activas para los filtros seleccionados.</div>:<div className="dash-alert-list">{data.recent_alerts.map(alert=><button key={alert.id} onClick={()=>onNavigate('Alertas')}><i className={alert.priority==='alta'?'red':'amber'}><AlertTriangle size={15}/></i><span><b>{alert.product}</b><small>{alert.description}</small></span><em>{alert.priority==='alta'?'Alta':'Media'}</em></button>)}</div>}</section>
    </div>
    {summary&&summary.transactions>0&&<p className="dashboard-footnote">Ventas históricas corresponden a comprobantes registrados. El pronóstico, cuando existe, es una estimación del modelo local y se muestra por separado.</p>}
  </div>
}
