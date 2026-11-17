(function(){
var H=window.YU_HISTORY,P=window.YU_PRICES,box=document.getElementById('chart');
if(!H||!P||!box)return;
var NAMES={sox:'SOX 費城半導體',ndx:'NDX 那斯達克100',twii:'TWII 台灣加權'};
var SZ={large:7,medium:5,small:3.4,none:3.6};
var cur='sox',W=860,Ht=420,m={l:52,r:12,t:12,b:26};
var tip=document.getElementById('tip');
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function px(d,ds,cs){ // 非交易日取下一個交易日
  var lo=0,hi=ds.length;while(lo<hi){var mid=(lo+hi)>>1;if(ds[mid]<d)lo=mid+1;else hi=mid}
  return lo<ds.length?lo:-1}
function draw(){
  var p=P[cur],n=p.d.length,t0=Date.parse(p.d[0]),t1=Date.parse(p.d[n-1]);
  var mn=Math.min.apply(null,p.c),mx=Math.max.apply(null,p.c),l0=Math.log(mn*.95),l1=Math.log(mx*1.05);
  function X(d){return m.l+(Date.parse(d)-t0)/(t1-t0)*(W-m.l-m.r)}
  function Y(v){return m.t+(1-(Math.log(v)-l0)/(l1-l0))*(Ht-m.t-m.b)}
  var s='<svg viewBox="0 0 '+W+' '+Ht+'" role="img" aria-label="'+NAMES[cur]+' 與加減碼訊號">';
  var ticks=[1,2,3,5,10,20,30,50,100,200].map(function(k){return k*1000}).concat([500,1000,2000,3000,5000,10000,20000,30000,50000]);
  ticks=ticks.filter(function(v,i,a){return a.indexOf(v)==i&&v>mn&&v<mx}).sort(function(a,b){return a-b});
  ticks.forEach(function(v){s+='<line x1="'+m.l+'" x2="'+(W-m.r)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="#2b3850" stroke-width="1"/><text x="'+(m.l-6)+'" y="'+(Y(v)+4)+'" text-anchor="end" fill="#a3aec0" font-size="11">'+v.toLocaleString()+'</text>'});
  for(var y=2020;y<=2026;y++){var xx=X(y+'-01-01');s+='<text x="'+xx+'" y="'+(Ht-8)+'" fill="#a3aec0" font-size="11">'+y+'</text>'}
  var d='';for(var i=0;i<n;i+=1){d+=(i?'L':'M')+X(p.d[i]).toFixed(1)+' '+Y(p.c[i]).toFixed(1)}
  s+='<path d="'+d+'" fill="none" stroke="#8fa6d8" stroke-width="1.3"/>';
  H.forEach(function(h,k){
    var a=h.action;if(a==='持平')return;
    var i=px(h.date,p.d,p.c);if(i<0)return;
    var x=X(p.d[i]),yv=Y(p.c[i]),r=SZ[h.size_class]||3.6,c,sh;
    if(a==='加碼'){c='#3fd0a0';sh='M'+x+' '+(yv-r*1.3)+'l'+r+' '+r*2+'h'+(-2*r)+'z'}
    else if(a==='減碼'||a==='出清'){c='#ff7482';sh='M'+x+' '+(yv+r*1.3)+'l'+r+' '+(-r*2)+'h'+(-2*r)+'z'}
    else{c='#8b95a7';sh=null}
    s+=sh?'<path data-k="'+k+'" d="'+sh+'" fill="'+c+'" fill-opacity=".85" stroke="#0f1724" stroke-width=".6"/>'
          :'<circle data-k="'+k+'" cx="'+x+'" cy="'+yv+'" r="2.4" fill="'+c+'" fill-opacity=".7"/>';
  });
  s+='</svg>';box.querySelector('.s').innerHTML=s;
}
function show(e){
  var t=e.target,k=t.getAttribute&&t.getAttribute('data-k');
  if(k==null){tip.style.display='none';return}
  var h=H[k],r=box.getBoundingClientRect();
  tip.innerHTML='<b>'+esc(h.date)+'　'+esc(h.action)+'</b><br>'+esc(h.size||'')+(h.size_class&&h.size_class!=='none'?'（'+h.size_class+'）':'')+(h.quote?'<br><span style="color:#a3aec0">「'+esc(h.quote)+'」</span>':'');
  tip.style.display='block';
  var x=e.clientX-r.left+12,y=e.clientY-r.top+12;
  if(x+tip.offsetWidth>r.width)x=e.clientX-r.left-tip.offsetWidth-12;
  tip.style.left=Math.max(4,x)+'px';tip.style.top=y+'px';
}
box.addEventListener('mousemove',show);box.addEventListener('click',show);box.addEventListener('mouseleave',function(){tip.style.display='none'});
document.querySelectorAll('.tabs button').forEach(function(b){b.addEventListener('click',function(){
  cur=b.dataset.i;document.querySelectorAll('.tabs button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});draw()})});
draw();
})();
