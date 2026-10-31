document.addEventListener('DOMContentLoaded', () => {
  if (!window.Chart) return;
  Chart.defaults.font.family = "'DM Sans', sans-serif";
  Chart.defaults.color = '#5f6f86';
  const line = document.getElementById('progressChart');
  if (line) new Chart(line, {type:'line',data:{labels:['Sep 22','Sep 29','Oct 6','Oct 13','Oct 20','Today'],datasets:[{label:'Overall Score',data:[61,64,63,69,73,78],borderColor:'#395886',backgroundColor:'rgba(99,142,203,.12)',fill:true,tension:.38,borderWidth:2.5,pointRadius:3,pointBackgroundColor:'#f0f3fa',pointBorderColor:'#395886',pointBorderWidth:2}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{backgroundColor:'#172b46',padding:12,displayColors:false,callbacks:{label:c=>`Score: ${c.raw}/100`}}},scales:{x:{grid:{display:false},border:{display:false},ticks:{font:{size:11}}},y:{min:40,max:100,ticks:{stepSize:20,font:{size:11}},grid:{color:'#e5ebf4'},border:{display:false}}}}});
  const radar = document.getElementById('radarChart');
  if (radar) new Chart(radar,{type:'radar',data:{labels:['Memory','Attention','Logic','Speed','Focus','Problem Solving'],datasets:[{label:'Current Profile',data:[82,71,86,64,74,78],backgroundColor:'rgba(99,142,203,.20)',borderColor:'#395886',pointBackgroundColor:'#148b80',borderWidth:2,pointRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{r:{min:0,max:100,ticks:{display:false,stepSize:25},grid:{color:'#d5deef'},angleLines:{color:'#d5deef'},pointLabels:{font:{size:11,weight:600},color:'#395886'}}}}});
});
