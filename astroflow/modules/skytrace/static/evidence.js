'use strict';
// Shared by the live application and the standalone report. All values come
// from persisted measurements; absent evidence is explicitly labelled.
window.AstroEvidence=(()=>{
 const ns='http://www.w3.org/2000/svg',gold='#edc86e',mint='#83e2cd',blue='#75bafb';
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const shape=(tag,attrs)=>{const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);return e;};
 const num=(n,d=2)=>Number.isFinite(n)?n.toLocaleString('de-DE',{maximumFractionDigits:d}):'nicht gespeichert';
 const button=(text,fn)=>{const b=node('button',text,'evidence-button');b.type='button';b.onclick=fn;return b;};
 function speed(c){const v=c.plausibility?.minimum_angular_speed_deg_s;return Number.isFinite(v)?v*3600:c.evidence?.minimum_angular_speed_arcsec_s;}
 function blinkText(b){return `${b.flashes} Blitze im gleichen Takt (alle ${num(b.period_pixels,0)} Analysepixel, ${b.form}) – typisch für Blitz- und Positionslichter eines Flugzeugs; ein taumelnder Satellit kann ähnlich aussehen`;}
 function classification(c){
  const names={meteor:'Meteorkandidat',plane:'Flugzeug-/Satellitenkandidat',satellite:'Satellitenkandidat',moving:'Bewegungskandidat',trail:'Spurkandidat'};
  // Passende Satellitenbahn: Namen nennen (bester Treffer nach Abstand und Zeitversatz).
  const sat=(c.catalogs||[]).find(r=>r.provider==='satellites'&&r.status==='compatible'&&r.matches?.length);
  if(sat){const m=sat.matches[0],what=m.object_type&&m.object_type!=='Satellit'?m.object_type:'Satellit',more=sat.matches.length>1?' (+'+(sat.matches.length-1)+' weitere Bahn'+(sat.matches.length>2?'en':'')+')':'';
   return {name:what+' '+(m.name||'NORAD '+m.norad_id),kind:'satellite',norad:m.norad_id,source:'Bahn passt in Lage, Richtung und Zeit (Abstand '+num((m.mean_offset_arcsec??m.separation_arcsec)/60,1)+'′'+(m.time_offset_s>0?', '+num(m.time_offset_s,1)+' s außerhalb der Belichtung':'')+')'+more+' – Kataloghinweis, Identität unbestätigt'};}
  const compatible=(c.catalogs||[]).filter(r=>r.status==='compatible').map(r=>({satellites:'satellite',aircraft:'plane',meteors:'meteor',fireballs:'meteor'})[r.provider]).filter(Boolean);
  const kinds=[...new Set(compatible)];if(kinds.length===1)return {name:names[kinds[0]],source:'Geometrischer Kataloghinweis; Identität unbestätigt',kind:kinds[0]};
  // Über mehrere Aufnahmen verkettete Spur: ein Meteor leuchtet nur in einer Aufnahme.
  if(c.linked_track?.count>1)return {name:'Bewegtes Objekt in '+c.linked_track.count+' Aufnahmen',kind:'plane',source:c.linked_track.reason};
  // Regelmäßig unterbrochene Spur: Blitz-/Positionslichter eines Flugzeugs (ein taumelnder Satellit kann ähnlich aussehen).
  if(c.blink)return {name:'Flugzeugkandidat (Blinklichter)',kind:'plane',source:blinkText(c.blink)};
  const hints=[...(c.model_hints||[])].filter(h=>names[h.kind]&&Number.isFinite(h.confidence)).sort((a,b)=>b.confidence-a.confidence);
  // Das Modell ist für Weitwinkel-Himmelskameras trainiert; im Teleskopbild bleibt seine Klasse ein Hinweis.
  if(hints.length&&c.model_scope==='narrow_field')return {name:names[c.kind]||'Spurkandidat',kind:c.kind||'trail',confidence:c.confidence,source:'Modellhinweis „'+names[hints[0].kind]+'“ ('+num(hints[0].confidence*100,0)+' %) – Modell für Weitwinkel-Himmelskameras trainiert, im Teleskopbild nur Hinweis'};
  if(hints.length)return {name:names[hints[0].kind],kind:hints[0].kind,confidence:hints[0].confidence,source:'Modellvorschlag; keine bestätigte Objektart'};
  return {name:names[c.kind]||'Spurkandidat',kind:c.kind||'trail',confidence:c.confidence,source:c.kind==='trail'||!c.kind?'Objektart noch offen':'Unbestätigter Erkennungsvorschlag'};
 }
 function displaySettings(){try{const s=JSON.parse(localStorage.getItem('stellina-overlay-display')||'{}');return {unit:s.unit==='kmh'?'kmh':'arcsec',distanceKm:Number.isFinite(s.distanceKm)&&s.distanceKm>0?s.distanceKm:null};}catch{return {unit:'arcsec',distanceKm:null};}}
 function setDisplaySettings(settings){const distanceKm=Number(settings.distanceKm);if(settings.unit==='kmh'&&(!Number.isFinite(distanceKm)||distanceKm<=0))throw Error('Für km/h bitte eine positive angenommene Entfernung in km eingeben.');const s={unit:settings.unit==='kmh'?'kmh':'arcsec',distanceKm:Number.isFinite(distanceKm)&&distanceKm>0?distanceKm:null};localStorage.setItem('stellina-overlay-display',JSON.stringify(s));return s;}
 function speedText(c){const v=speed(c),s=displaySettings();if(!Number.isFinite(v))return 'Geschwindigkeit unbekannt';if(s.unit==='kmh'){if(!s.distanceKm)return 'km/h: Entfernung fehlt';return '≥ '+num(v/3600*Math.PI/180*s.distanceKm*3600,1)+' km/h*';}return '≥ '+num(v,1)+'″/s';}
 function setOverlayVisible(svg,visible){
  // SVGElement.hidden does not reflect the HTML hidden attribute in all browsers.
  svg.hidden=!visible;
  if(visible)svg.removeAttribute('hidden');else svg.setAttribute('hidden','');
  svg.style.display=visible?'':'none';
 }
 function motionLabel(svg,c,points,width,height,settled=false){
  if(!points?.length)return;
  const type=classification(c),label=c.id+' · '+type.name+' · '+speedText(c);
  const renderedWidth=Math.min(svg.clientWidth||width,(svg.clientHeight||height)*width/height);
  const unit=width/Math.max(1,renderedWidth),w=Math.min(width-8,Math.max(170,label.length*8)*unit),h=25*unit;
  const center=points[Math.floor(points.length/2)],x=Math.max(4,Math.min(width-w-4,center[0]-w/2));
  let y=Math.max(4,Math.min(height-h-4,center[1]-h-9*unit));
  if(!svg.querySelector?.('.motion-label'))svg._labelBoxes=[];
  const boxes=(svg._labelBoxes||[]).filter(b=>b.id!==c.id);
  for(let attempt=0;attempt<boxes.length;attempt++){const hit=boxes.find(b=>x<b.x+b.w&&x+w>b.x&&y<b.y+b.h&&y+h>b.y);if(!hit)break;y=Math.max(4,hit.y-h-4*unit);}
  boxes.push({id:c.id,x,y,w,h});svg._labelBoxes=boxes;
  const group=shape('g',{class:'motion-label',tabindex:0,role:'img','aria-label':label,transform:`translate(${x} ${y})`});
  if(points.length>1)group.append(shape('polyline',{points:points.map(p=>[p[0]-x,p[1]-y].join(',')).join(' '),fill:'none',stroke:'transparent','stroke-width':14*unit,'pointer-events':'stroke'}));
  const background=shape('rect',{width:w,height:h}),text=shape('text',{x:8*unit,y:18*unit});text.style.fontSize=14*unit+'px';text.style.strokeWidth=3*unit+'px';text.textContent=label;group.append(background,text);
  const settings=displaySettings(),p=c.plausibility||{},lines=[type.name+' · '+type.source,...(Number.isFinite(type.confidence)?['Modellkonfidenz '+num(type.confidence*100,1)+' %; keine Identitätswahrscheinlichkeit']:[]),settings.unit==='kmh'?'* Mindest-Schätzung bei angenommener Entfernung: '+num(settings.distanceKm)+' km':'Mindest-Winkelgeschwindigkeit, keine km/h',`Spur: ${num(p.angular_length_deg,4)}° · Belichtung: ${num(c.exposure_seconds)} s`,'Sichtbarkeit kann kürzer als die Belichtung sein.',...((p.scenarios||[]).map(s=>`${s.kind==='aircraft'?'Flugzeug':s.kind==='satellite'?'Satellit':'Meteor'} bei ${num(s.distance_km,0)} km: ${num(s.required_visibility_seconds,4)} s`)),'Reale Entfernung und Geschwindigkeit sind ungemessen.'];
  const maxChars=Math.max(20,Math.floor(Math.min(width/unit-16,390)/6));
  const wrapped=lines.flatMap(line=>{const result=[];let rest=line;while(rest.length>maxChars){let cut=rest.lastIndexOf(' ',maxChars);if(cut<1)cut=maxChars;result.push(rest.slice(0,cut));rest=rest.slice(cut).trim();}result.push(rest);return result;});
  const tipW=Math.min(width-8,410*unit),tipH=(wrapped.length*18+14)*unit,tipX=Math.max(-x+4,Math.min(0,width-x-tipW-4)),tipY=y+h+tipH<height?h+3*unit:-tipH-3*unit;
  const tip=shape('g',{class:'motion-tooltip',transform:`translate(${tipX} ${Math.max(-y+4,tipY)})`});tip.append(shape('rect',{width:tipW,height:tipH,rx:6*unit}));
  wrapped.forEach((line,i)=>{const t=shape('text',{x:8*unit,y:(20+i*18)*unit});t.style.fontSize=11*unit+'px';t.textContent=line;tip.append(t);});group.append(tip);group.onclick=e=>e.stopPropagation();svg.append(group);
  if(!settled&&!svg.clientWidth&&typeof requestAnimationFrame!=='undefined')requestAnimationFrame(()=>{if(group.isConnected){group.remove();motionLabel(svg,c,points,width,height,true);}});
 }
 const satelliteColors=[blue,'#b6a0ed','#ee9b86','#a3c97c','#83a9ce','#dba8d4','#a9c1c1','#d7ad83'];
 function drawSatellites(svg,tracks,width,height){
  if(!tracks?.length)return;
  const unit=width/Math.max(1,svg.clientWidth||width),group=shape('g',{class:'satellite-overlay'});
  tracks.forEach((track,index)=>{
   const color=satelliteColors[index%satelliteColors.length];
   if(!track.in_frame)return;
   for(const points of track.segments||[]){
    group.append(shape('polyline',{points:points.map(p=>p.join(',')).join(' '),fill:'none',stroke:color,'stroke-width':2,'stroke-dasharray':'7 5','vector-effect':'non-scaling-stroke'}));
    const point=points.find(p=>p[0]>=0&&p[0]<=width&&p[1]>=0&&p[1]<=height);
    if(point){const label=shape('text',{x:Math.max(4,Math.min(width-180*unit,point[0]+8*unit)),y:Math.max(16*unit,Math.min(height-12*unit,point[1]-8*unit)),class:'satellite-name'});label.textContent=track.name+' · Prognose';label.style.fill=color;label.style.fontSize=12*unit+'px';label.style.strokeWidth=3*unit+'px';group.append(label);}
   }
  });
  const outside=tracks.filter(t=>!t.in_frame).length,note=shape('text',{x:12*unit,y:52*unit,class:'satellite-name'});
  note.textContent='Satellitenprognose'+(tracks[0].frame?' · Bild '+tracks[0].frame:'')+(outside?' · '+outside+' Bahn'+(outside===1?'':'en')+' außerhalb des Bildfelds':'');note.style.fontSize=12*unit+'px';note.style.strokeWidth=3*unit+'px';group.append(note);svg.append(group);
 }
 function analysisDetails(c,host){const body=node('div',undefined,'analysis-details');host.append(body);render(c,body);return body;}
 function videoOverlay(video,svg,data,candidates,at=()=>video.currentTime,selected=()=>null){
  const draw=()=>{svg.replaceChildren();if(svg.hidden||!data?.frames?.length)return;svg.setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);
   const surface=video.hidden?video.parentElement?.querySelector('img')||video:video;
   const vw=data.width,vh=data.height,cw=surface.clientWidth,ch=surface.clientHeight;if(cw&&ch){const scale=Math.min(cw/vw,ch/vh);svg.style.width=vw*scale+'px';svg.style.height=vh*scale+'px';svg.style.left=(cw-vw*scale)/2+'px';svg.style.top=(ch-vh*scale)/2+'px';}
   // Binary search keeps long recordings responsive, including seeking backwards.
   let lo=0,hi=data.frames.length;while(lo<hi){const mid=(lo+hi)>>1;if(data.frames[mid].seconds<=at())lo=mid+1;else hi=mid;}
   const frame=data.frames[Math.max(0,lo-1)],unit=data.width/Math.max(1,svg.clientWidth||data.width);
   for(const object of frame.objects||[]){
    if(object.x<0||object.y<0||object.x>data.width||object.y>data.height)continue;
    const g=shape('g',{class:'plate-object',role:'img','aria-label':object.name}),title=shape('title',{});title.textContent=object.name+' · Plate Solving · RA '+num(object.ra,5)+'° · Dec '+num(object.dec,5)+'°';
    g.append(title,shape('circle',{cx:object.x,cy:object.y,r:13*unit,fill:'none',stroke:'#66b6d5','stroke-width':1.2,'vector-effect':'non-scaling-stroke'}));
    const text=shape('text',{x:Math.max(4,Math.min(object.x+17*unit,data.width-Math.min(data.width-8,object.name.length*7*unit))),y:Math.max(18*unit,object.y-8*unit),class:'plate-object-name'});text.textContent=object.name;text.style.fontSize=14*unit+'px';text.style.strokeWidth=3*unit+'px';g.append(text);svg.append(g);
   }
   const selectedId=selected()||candidates[0]?.id;
   drawSatellites(svg,(frame.satellites||[]).filter(s=>s.candidate_id===selectedId),data.width,data.height);
   for(const trail of frame.trails){const c=candidates.find(c=>c.id===trail.id);if(!c)continue;svg.append(shape('polyline',{points:trail.points.map(p=>p.join(',')).join(' '),fill:'none',stroke:gold,'stroke-width':2,'vector-effect':'non-scaling-stroke'}));motionLabel(svg,c,trail.points,data.width,data.height);}
  };
  if(video._motionCleanup)video._motionCleanup();
  for(const event of ['timeupdate','seeked','loadedmetadata','play'])video.addEventListener(event,draw);
  const observer=typeof ResizeObserver!=='undefined'?new ResizeObserver(draw):null;if(observer){observer.observe(video);if(video.parentElement)observer.observe(video.parentElement);}
  const previewImage=video.parentElement?.querySelector('img');previewImage?.addEventListener('load',draw);
  video._motionCleanup=()=>{for(const event of ['timeupdate','seeked','loadedmetadata','play'])video.removeEventListener(event,draw);previewImage?.removeEventListener('load',draw);observer?.disconnect();};
  // Native timeupdate is too sparse at 8 fps; repaint on decoded video frames.
  let frameRequest;
  if(video.requestVideoFrameCallback){const tick=()=>{draw();frameRequest=video.requestVideoFrameCallback(tick);};frameRequest=video.requestVideoFrameCallback(tick);const cleanup=video._motionCleanup;video._motionCleanup=()=>{cleanup();video.cancelVideoFrameCallback(frameRequest);};}
  draw();return draw;
 }
 let dialog=null,serial=0;
 function inspection(title){
  if(dialog){dialog.close();dialog.remove();}
  dialog=node('dialog',undefined,'inspection-dialog');const head=node('div',undefined,'inspection-head'),label=node('strong',title),close=button('Schließen ×',()=>dialog.close()),tools=node('div',undefined,'inspection-tools'),viewport=node('div',undefined,'inspection-viewport'),canvas=node('div',undefined,'inspection-canvas');
  head.append(label,close);viewport.append(canvas);dialog.append(head,tools,viewport);document.body.append(dialog);dialog.showModal();
  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});
  let scale=1,rotation=false,w=1,h=1,content=null;
  const zoomLabel=node('span');
  function draw(){if(!content)return;const dw=w*scale,dh=h*scale;content.style.width=dw+'px';content.style.height=dh+'px';content.style.transform=rotation?`translate(${dh}px,0) rotate(90deg)`:'none';canvas.style.width=(rotation?dh:dw)+'px';canvas.style.height=(rotation?dw:dh)+'px';zoomLabel.textContent=Math.round(scale*100)+' %';}
  function fit(){scale=Math.min((viewport.clientWidth-36)/(rotation?h:w),(viewport.clientHeight-36)/(rotation?w:h));scale=Math.max(.05,scale);draw();}
  tools.append(button('−',()=>{scale=Math.max(.05,scale/1.5);draw();}),zoomLabel,button('+',()=>{scale=Math.min(16,scale*1.5);draw();}),button('Einpassen',fit),button('1:1 Pixel',()=>{scale=1;draw();}),button('90° drehen',()=>{rotation=!rotation;fit();}));
  let dragging=null;
  viewport.onpointerdown=e=>{if(e.button!==0)return;dragging={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);viewport.style.cursor='grabbing';};
  viewport.onpointermove=e=>{if(dragging){viewport.scrollLeft=dragging.left+dragging.x-e.clientX;viewport.scrollTop=dragging.top+dragging.y-e.clientY;}};
  viewport.onpointerup=viewport.onpointercancel=()=>{dragging=null;viewport.style.cursor='grab';};
  viewport.onwheel=e=>{if(!e.ctrlKey)return;e.preventDefault();scale=Math.max(.05,Math.min(16,scale*(e.deltaY<0?1.15:1/1.15)));draw();};
  return {tools,label,set(element,width,height,reset=true){content=element;content.classList.add('inspection-content');content.style.transformOrigin='top left';canvas.replaceChildren(content);w=width;h=height;if(reset)fit();else draw();},fit};
 }
 function openImage(c,index=0,overlay=true){
  const viewer=inspection(c.id+' · Originalausschnitt'),toggle=node('input');toggle.type='checkbox';toggle.checked=overlay;const label=node('label');label.append(toggle,document.createTextNode(' Overlay'));const frameLabel=node('span');
  viewer.tools.append(button('← Vorher',()=>show(index-1,false)),button('Nachher →',()=>show(index+1,false)),label,frameLabel);
  function show(i,reset){index=(i+c.frames.length)%c.frames.length;const f=c.frames[index],group=node('div',undefined,'inspection-image'),image=node('img');image.src=f.url||f.image;image.alt=c.id+' · Bild '+f.index;image.draggable=false;group.append(image);
   const svg=shape('svg',{viewBox:`0 0 ${f.width} ${f.height}`,preserveAspectRatio:'none'});svg.style.display=toggle.checked?'':'none';const o=f.overlay;
   if(o?.points?.length>1)svg.append(shape('polyline',{points:o.points.map(p=>p.join(',')).join(' '),fill:'none',stroke:gold,'stroke-width':2,'vector-effect':'non-scaling-stroke'}));
   else if(o?.points?.length)svg.append(shape('circle',{cx:o.points[0][0],cy:o.points[0][1],r:8,fill:'none',stroke:gold,'stroke-width':2}));
   if(o?.box){const [x,y,width,height]=o.box;svg.append(shape('rect',{x,y,width,height,fill:'none',stroke:gold,'stroke-width':2}));}
   if(o?.points?.length)motionLabel(svg,c,o.points,f.width,f.height);
   group.append(svg);toggle.onchange=()=>svg.style.display=toggle.checked?'':'none';frameLabel.textContent=`Bild ${f.index} · ${f.timestamp_text||''}${f.hit?' · Treffer':' · Kontext'}`;viewer.set(group,f.width,f.height,reset);}
  show(index,true);
 }
 function chart(title,series,xlabel,ylabel,focus=null){
  const box=node('div',undefined,'evidence-chart'),heading=node('div',undefined,'evidence-chart-heading'),svg=shape('svg',{viewBox:'0 0 760 300',role:'img','aria-label':title}),legend=node('p',undefined,'evidence-legend');
  heading.append(node('h4',title),button('Vergrößern ↗',()=>{const viewer=inspection(title);viewer.tools.append(legend.cloneNode(true));viewer.set(svg.cloneNode(true),760,300);}));box.append(heading,svg,legend);
  let limits=focus;
  function draw(){
   svg.replaceChildren();const values=series.flatMap(s=>s.points).filter(p=>p.every(Number.isFinite));if(!values.length){box.append(node('p','Keine belastbaren Messwerte verfügbar.'));return;}
   let xmin=Math.min(...values.map(p=>p[0])),xmax=Math.max(...values.map(p=>p[0])),ymin=Math.min(0,...values.map(p=>p[1])),ymax=Math.max(...values.map(p=>p[1]));
   if(limits)[xmin,xmax,ymin,ymax]=limits;
   const dx=xmax-xmin||1,dy=ymax-ymin||1;xmin-=dx*.06;xmax+=dx*.06;ymin-=dy*.12;ymax+=dy*.12;
   if(focus){const cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,w=Math.max(xmax-xmin,(ymax-ymin)*675/218),h=w*218/675;xmin=cx-w/2;xmax=cx+w/2;ymin=cy-h/2;ymax=cy+h/2;}
   const x=v=>66+(v-xmin)/(xmax-xmin)*675,y=v=>248-(v-ymin)/(ymax-ymin)*218;
   const clipId='plot-'+(++serial),defs=shape('defs',{}),clip=shape('clipPath',{id:clipId});clip.append(shape('rect',{x:66,y:30,width:675,height:218}));defs.append(clip);svg.append(defs);
   for(let i=0;i<=4;i++){const xv=xmin+(xmax-xmin)*i/4,yv=ymin+(ymax-ymin)*i/4;svg.append(shape('line',{x1:x(xv),x2:x(xv),y1:30,y2:248,stroke:'#ffffff12'}),shape('line',{x1:66,x2:741,y1:y(yv),y2:y(yv),stroke:'#ffffff12'}));const tx=shape('text',{x:x(xv),y:267,'text-anchor':'middle'});tx.textContent=num(xv,1);const ty=shape('text',{x:59,y:y(yv)+4,'text-anchor':'end'});ty.textContent=num(yv,1);svg.append(tx,ty);}
   const traces=shape('g',{'clip-path':`url(#${clipId})`});
   for(const s of series){const points=s.points.filter(p=>p.every(Number.isFinite));if(!points.length)continue;const coordinates=points.map(p=>x(p[0])+','+y(p[1])).join(' ');if(s.band)traces.append(shape('polyline',{points:coordinates,fill:'none',stroke:s.color,'stroke-width':s.band*2*675/(xmax-xmin),'stroke-opacity':.12,'stroke-linecap':'round','stroke-linejoin':'round'}));const line=shape('polyline',{points:coordinates,fill:'none',stroke:s.color,'stroke-width':s.width||2.5,'stroke-dasharray':s.dash||''});const hint=shape('title',{});hint.textContent=s.name;line.append(hint);traces.append(line);for(const p of points)if(points.length<30)traces.append(shape('circle',{cx:x(p[0]),cy:y(p[1]),r:3,fill:s.color}));}
   svg.append(traces);const xl=shape('text',{x:404,y:293,'text-anchor':'middle'});xl.textContent=xlabel;const yl=shape('text',{x:15,y:140,transform:'rotate(-90 15 140)','text-anchor':'middle'});yl.textContent=ylabel;svg.append(xl,yl);
  }
  for(const s of series){const label=node('span',s.name);label.style.color=s.color;legend.append(label);}
  if(focus)heading.append(button('Spur / gesamtes Umfeld',()=>{limits=limits?null:focus;draw();}));
  draw();return box;
 }
 function skyPoint(world,origin){const r=Math.PI/180,a=world[0]*r,d=world[1]*r,a0=origin[0]*r,d0=origin[1]*r,den=Math.sin(d0)*Math.sin(d)+Math.cos(d0)*Math.cos(d)*Math.cos(a-a0);if(den<=0)return [NaN,NaN];return [Math.cos(d)*Math.sin(a-a0)/den/r*60,(Math.cos(d0)*Math.sin(d)-Math.sin(d0)*Math.cos(d)*Math.cos(a-a0))/den/r*60];}
 function render(c,host){
  const section=node('section',undefined,'evidence-panel');section.append(node('h3','Warum wurde diese Spur erkannt?'));
  const e=c.evidence;
  if(!e){section.append(node('p','Für diesen älteren Kandidaten fehlen die gespeicherten Messdetails. „Zeitreihen aktualisieren“ ergänzt sie.'));host.append(section);return;}
  const q=e.quality||{},r=e.registration||{},cards=node('div',undefined,'evidence-metrics');
  const metrics=[['Detektorwert',num(e.detector_score),'Schwelle '+num(e.detector_threshold)+' · keine Wahrscheinlichkeit'],['Spurlänge',num(e.length_pixels,1)+' px',Number.isFinite(e.length_arcmin)?num(e.length_arcmin,2)+' Bogenminuten':'Keine belastbaren Himmelskoordinaten'],['Sternregistrierung',r.accepted?num(r.inliers??r.matched,0)+' Sterne':'unsicher','Medianfehler '+num(r.median_error,3)+' px'],['Zeitlicher Nachweis',num(e.hit_frames,0)+' Trefferbild(er)',num(c.exposure_seconds)+' s Belichtung pro Bild']];
  for(const [name,value,note]of metrics){const card=node('div');card.append(node('small',name),node('strong',value),node('p',note));cards.append(card);}section.append(cards);
  const explanations=node('ul');
  const reasons=[Number.isFinite(e.detector_score)?`Die Linienintegration ergibt ${num(e.detector_score)} bei einer Erkennungsschwelle von ${num(e.detector_threshold)}. Der Wert beschreibt den integrierten Linienkontrast und ist weder eine Prozentwahrscheinlichkeit noch eine kalibrierte Sigma-Signifikanz.`:c.confidence!=null?`Das Erkennungsmodell meldet ${num(c.confidence*100,1)} % Modellkonfidenz. Das bestätigt keinen Objekttyp.`:'Kein numerischer Detektorwert gespeichert.',
   r.accepted?`${num(r.inliers??r.matched,0)} Referenzsterne stützen die Sternfeldkorrektur. Der Medianfehler beträgt ${num(r.median_error,3)} px; gemeinsame Teleskopbewegung wurde berücksichtigt.`:'Die Sternfeldzuordnung ist unsicher. Sternreste und Bewegungsartefakte bleiben mögliche Ursachen.',
   q.excluded?'Das Trefferbild wurde als unscharf markiert; die Bewertung ist entsprechend eingeschränkt.':`Bildqualität: ${q.reason||'keine Bewertung gespeichert'}. Sternachsenverhältnis ${num(q.axis_ratio,3)}, FWHM ${num(q.fwhm,3)} Analysepixel.`,
   c.blink?`${num(e.hit_frames,0)} Bild zeigt die Erkennung. ${blinkText(c.blink)}. Ein Meteor zieht eine durchgehende Spur.`
   :c.linked_track?.count>1?`${num(e.hit_frames,0)} Bilder zeigen die Erkennung (Bilder ${c.linked_track.frames.join(', ')}, über ${num(c.linked_track.span_seconds,0)} s). ${c.linked_track.reason} Satellit oder Flugzeug sind dadurch noch nicht unterschieden.`
   :`${num(e.hit_frames,0)} Bild(er) zeigen die Erkennung. Die Spur ist ein Kandidat; Meteor, Satellit, Flugzeug oder ein verbliebenes Artefakt sind dadurch noch nicht unterschieden.`];
  if(Number.isFinite(e.minimum_angular_speed_arcsec_s))reasons.push(`Die gemessene Winkellänge würde bei Ausnutzung der gesamten Belichtung mindestens ${num(e.minimum_angular_speed_arcsec_s,1)} Bogensekunden/s entsprechen. Bei kürzerer Leuchtdauer wäre sie schneller. Entfernung, reale Geschwindigkeit und Bewegungsrichtung sind aus einer Einzelspur nicht bestimmt.`);
  if(q.tail_reference_stars)reasons.push(`Sternschweifprüfung: ${num(q.tail_stars,0)} von ${num(q.tail_reference_stars,0)} untersuchten Sternen mit langen schwachen Ausläufern; Richtungskohärenz ${num(q.tail_direction_coherence,3)}, verteilt über ${num(q.tail_quadrants,0)} Bildquadranten. Gemeinsame Ausläufer sprechen für Bewegung während der Belichtung; eine reine Verschiebung zwischen Aufnahmen reicht dafür nicht.`);
  const form=e.shape_validation,burst=e.frame_validation;
  if(form?.status==='measured')reasons.push(`Formprüfung: maximale Abweichung der vermessenen Spurlinie ${num(form.bend_pixels,2)} px bei Grenzwert ${num(form.bend_limit_pixels,2)} px, ${num(form.supported_bins,0)} vermessene Abschnitte. ${form.curved?'Deutlich gekrümmte Form.':'Keine deutlich gekrümmte Form festgestellt.'}`);
  if(burst?.simultaneous_detections>=3)reasons.push(`${num(burst.simultaneous_detections,0)} gleichzeitige Erkennungen in diesem Bild. ${burst.note}`);
  for(const reason of reasons)explanations.append(node('li',reason));section.append(explanations);
  const p=c.plausibility;
  if(p){
   section.append(node('h4','Physikalische Vorprüfung · Bildfeld und Belichtung'));
   const fov=p.field_of_view_deg;
   section.append(node('p',`Original: ${(p.original_size||[]).map(v=>v||'?').join(' × ')} Pixel · Bildfeld: ${fov?fov.map(v=>num(v,3)+'°').join(' × '):'nicht kalibriert'} · Belichtung: ${Number.isFinite(p.exposure_seconds)?num(p.exposure_seconds)+' s ('+p.exposure_source+')':p.exposure_status==='conflicting'?'widersprüchliche Headerwerte':'nicht eindeutig im Header'}.`));
   section.append(node('p',`Spurlänge: ${num(p.angular_length_deg,4)}° · Mindest-Winkelgeschwindigkeit: ${num(p.minimum_angular_speed_deg_s,4)}°/s. Winkelmaß aus: ${p.angular_source||'nicht verfügbar'}.`));
   if(p.touches_border)section.append(node('p','Spur erreicht den Bildrand: die gemessene Länge kann nur ein Ausschnitt der tatsächlichen Bahn sein.','evidence-note'));
   if(p.scenarios?.length){
    const table=node('table',undefined,'evidence-table'),head=node('tr');
    for(const label of ['Vergleichsszenario','Angenommene Querbewegung','Zeit für gemessene Spur','Vorprüfung'])head.append(node('th',label));
    const thead=node('thead');thead.append(head);table.append(thead);const body=node('tbody');
    for(const s of p.scenarios){const row=node('tr');for(const text of [s.label,num(s.transverse_speed_km_s,3)+' km/s',num(s.required_visibility_seconds,4)+' s',s.verdict])row.append(node('td',text));body.append(row);}table.append(body);
    const scroll=node('div',undefined,'evidence-table-scroll');scroll.append(table);section.append(scroll);
   }else section.append(node('p','Ohne sichere Belichtungszeit und Winkelmaß kein Geschwindigkeitsvergleich.','evidence-note'));
   section.append(node('p',p.limitations,'evidence-note'));
  }
  const graphs=node('div',undefined,'evidence-graphs');
  if(e.profile){graphs.append(chart('Helligkeitsprofil quer zur Spur',[{name:'Trefferbild · Seitenhintergrund abgezogen',color:gold,points:e.profile.offset_pixels.map((x,i)=>[x,e.profile.contrast_adu[i]])}],'Abstand zur erkannten Linie [Analysepixel]','Linienkontrast [ADU]'));}
  const temporal=(e.temporal||[]).filter(t=>Number.isFinite(t.contrast_adu)&&Number.isFinite(t.timestamp));
  if(temporal.length){const zero=c.timestamp;graphs.append(chart('Spurkontrast im zeitlichen Umfeld',[{name:'Gleiche sternregistrierte Position',color:mint,points:temporal.map(t=>[t.timestamp-zero,t.contrast_adu])}],'Zeit relativ zum Treffer [s]','Linienkontrast [ADU]'));}
  section.append(graphs,node('p','Profile: Medianmessung entlang der Linie nach Sensorbandkorrektur, relativ zum lokalen Seitenhintergrund. Helle Sterne können Restbeiträge liefern. Das sind ergänzende Diagnosewerte, nicht der ursprüngliche Detektorwert. Fehlende oder unscharfe Kontextmessungen werden ausgelassen.','evidence-note'));
  const sat=(c.catalogs||[]).find(r=>r.provider==='satellites'),diagnostics=sat?.diagnostics||[];
  if(diagnostics.length){
   section.append(node('h4','Gemessene Spur und Satellitenbahnen im Aufnahmebild'));
   const context=c.context_image,projection=c.satellite_overlay;
   if(context&&(context.url||context.image)){
    const stage=node('div',undefined,'satellite-comparison'),image=node('img'),svg=shape('svg',{viewBox:`0 0 ${context.width} ${context.height}`,class:'satellite-image-overlay','aria-label':'Gemessene Spur und projizierte Satellitenbahnen'});
    const frameUrl=c.frames?.find(f=>f.url)?.url;
    image.src=context.url||(frameUrl?frameUrl.replace(/\/kandidaten\/[^/]+\/[^/]+$/,'/'+context.image.split('/').pop()):context.image);image.alt='Originalaufnahme · Bild '+context.frame+' · '+(context.timestamp_text||'');stage.append(image,svg);section.append(stage);
    const draw=()=>{svg.replaceChildren();const master=document.querySelector('#overlayToggle')||document.querySelector('#globalOverlay');if(master)setOverlayVisible(svg,master.checked);drawSatellites(svg,(projection?.paths||[]).map(p=>({...p,frame:context.frame})),context.width,context.height);
     if(context.points?.length>1)svg.append(shape('polyline',{points:context.points.map(p=>p.join(',')).join(' '),fill:'none',stroke:gold,'stroke-width':3,'vector-effect':'non-scaling-stroke'}));
     motionLabel(svg,c,context.points,context.width,context.height);
    };image.onload=draw;draw();
    if(typeof ResizeObserver!=='undefined'){
     const observer=new ResizeObserver(()=>{if(stage.isConnected)draw();else observer.disconnect();});observer.observe(stage);
    }
   }
   section.append(node('p',(projection?.reason||'Gespeicherte Bildprojektion fehlt. Zeitreihen aktualisieren, um sie zu ergänzen.')+' Gold: tatsächlich gemessene Spur. Gestrichelt: Katalogprognosen für dieselbe Belichtung samt Uhrtoleranz. Bahnen außerhalb des Bildfelds werden nicht in die Aufnahme verschoben.','evidence-note'));
   if(projection?.paths?.length){const legend=node('ul',undefined,'satellite-image-legend');projection.paths.forEach((p,i)=>{const entry=node('li',p.name+' · '+(p.in_frame?'im Bildfeld':'außerhalb des Bildfelds'));entry.style.color=satelliteColors[i%satelliteColors.length];legend.append(entry);});section.append(legend);}
   const table=node('table',undefined,'evidence-table'),thead=node('thead'),tr=node('tr');for(const text of ['Geprüfte Bahn','Abstand zur Spur','Abdeckung','Richtungsabweichung','Durchgang','Bahnalter','Bewertung'])tr.append(node('th',text));thead.append(tr);table.append(thead);const tbody=node('tbody');
   const passage=d=>!Array.isArray(d.passage_seconds)||d.separation_arcsec==null||d.coverage<.5?'–':d.time_offset_s>0?num(d.time_offset_s,1)+' s außerhalb der Belichtung':'während der Belichtung';
   for(const d of diagnostics){const row=node('tr');for(const text of [(d.name||'NORAD '+d.norad_id)+(d.object_type&&d.object_type!=='Satellit'?' ('+d.object_type+')':''),d.separation_arcsec==null?'> 10° ab Spuranfang':num(d.separation_arcsec/60,2)+'′',num(d.coverage*100,0)+' %',d.alignment_degrees==null?'nicht auswertbar':num(d.alignment_degrees,1)+'°',passage(d),num(d.tle_age_days,2)+' Tage',d.compatible?'geometrisch vereinbar':(d.reasons||[]).join('; ')||'Kriterien nicht erfüllt'])row.append(node('td',text));tbody.append(row);}table.append(tbody);const scroll=node('div',undefined,'evidence-table-scroll');scroll.append(table);section.append(scroll);
   if(sat.names_note)section.append(node('p',sat.names_note,'evidence-note'));
  }
  const data=node('details'),summary=node('summary','Alle gespeicherten Messdaten und Grenzen');data.append(summary,node('pre',JSON.stringify({method:c.method,evidence:e,world_path:c.world_path,catalogs:c.catalogs},null,2)));section.append(data);host.append(section);
 }
 return {render,openImage,skyPoint,motionLabel,analysisDetails,hoverDetails:analysisDetails,videoOverlay,speed,setOverlayVisible,classification,candidateName:c=>classification(c).name,displaySettings,setDisplaySettings,speedText,drawSatellites};
})();
