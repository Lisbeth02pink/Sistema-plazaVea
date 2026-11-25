import LocalForecastPanel from './LocalForecastPanel';
import './forecast-phase5.css';

export default function ForecastPage({canTrain}:{canTrain:boolean}){
  return <div className="content forecast-content">
    <div className="title-row"><div><h1>Predicción de demanda con IA</h1><p>Un modelo local aprende del historial de ventas y considera el inventario para anticipar faltantes y exceso.</p></div><span className="forecast-version local-heading-badge"><i/>Scikit-learn · PostgreSQL</span></div>
    <LocalForecastPanel canTrain={canTrain}/>
  </div>
}
