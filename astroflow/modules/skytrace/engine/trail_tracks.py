"""Link measured single-frame streaks of one moving object across exposures.

A meteor glows for a fraction of a second and therefore lies in exactly one
exposure. Collinear streaks with the same direction whose positions advance at
the rate implied by their own length per exposure belong to one slowly moving
object (satellite, aircraft); such a chain is never a meteor candidate.
M74 2022-08-07, img-0177…0182: six segments of ~140 px/10 s, 15°, linked.
"""
import math

import numpy as np

from motion_plausibility import exposure_info

MAX_GAP_SECONDS=180      # excluded (blurred) exposures may lie in between
PARALLEL=math.cos(math.radians(8))
RATE_RANGE=(.55,1.6)     # measured advance / expected advance (segments may be clipped)


def _segments(rows):
    items=[]
    for row in rows:
        if row.get('timestamp') is None: continue
        exposure=exposure_info(row.get('meta') or {})['seconds']
        if not exposure: continue
        for detection in row.get('detections') or []:
            segment=detection.get('segment')
            if not segment or detection.get('track_id') is not None: continue
            a,b=np.asarray(segment,dtype=float)
            length=float(np.linalg.norm(b-a))
            if length<20: continue
            items.append(dict(row=row,detection=detection,time=float(row['timestamp']),mid=(a+b)/2,
                              direction=(b-a)/length,length=length,rate=length/exposure))
    return items


def _compatible(p,q):
    dt=q['time']-p['time']
    if not 0<dt<=MAX_GAP_SECONDS or q['row']['index']==p['row']['index']: return False
    if abs(float(np.dot(p['direction'],q['direction'])))<PARALLEL: return False
    shift=q['mid']-p['mid']
    along=abs(float(np.dot(shift,p['direction'])))
    across=abs(float(shift[0]*p['direction'][1]-shift[1]*p['direction'][0]))
    if across>20+.03*along: return False
    expected=max(p['rate'],q['rate'])*dt
    return RATE_RANGE[0]*expected<=along<=RATE_RANGE[1]*expected


def _uniform(chain):
    """One object moves uniformly: midpoints must fit position = p0 + v·t.

    Segments clipped at the border or detected only in part shift their midpoint by
    up to half a streak length (M74: RMS 39 px for 94–143 px segments)."""
    t=np.asarray([c['time'] for c in chain]); t-=t.mean()
    mids=np.asarray([c['mid'] for c in chain])
    if len(chain)==2: return True
    design=np.column_stack([np.ones_like(t),t])
    coefficients,*_=np.linalg.lstsq(design,mids,rcond=None)
    residual=float(np.sqrt(np.mean(np.sum((design@coefficients-mids)**2,axis=1))))
    return residual<=25+.5*max(c['length'] for c in chain)


def link_tracks(rows):
    """Mark linked streaks in place (`track_id`, kind `plane`, `linked_track`); returns the number of tracks."""
    items=sorted(_segments(rows),key=lambda item:item['time'])
    parent=list(range(len(items)))
    def root(i):
        while parent[i]!=i: parent[i]=parent[parent[i]]; i=parent[i]
        return i
    for i,p in enumerate(items):
        for j in range(i+1,len(items)):
            if items[j]['time']-p['time']>MAX_GAP_SECONDS: break
            if _compatible(p,items[j]): parent[root(j)]=root(i)
    groups={}
    for i in range(len(items)): groups.setdefault(root(i),[]).append(items[i])
    count=0
    for chain in groups.values():
        frames=sorted({c['row']['index'] for c in chain})
        if len(frames)<2: continue
        # Per frame keep the strongest streak; parallel fragments of one exposure are the same streak.
        chain=sorted(chain,key=lambda c:(c['row']['index'],-(c['detection'].get('line_snr') or 0)))
        if not _uniform([c for k,c in enumerate(chain) if k==0 or c['row']['index']!=chain[k-1]['row']['index']]): continue
        count+=1
        span=chain[-1]['time']-chain[0]['time']
        info=dict(frames=[f+1 for f in frames],count=len(frames),span_seconds=round(span,1),
                  rate_pixels_s=round(float(np.median([c['rate'] for c in chain])),2),
                  direction_degrees=round(math.degrees(math.atan2(*chain[0]['direction'][::-1])),1),
                  reason=f'Gleichgerichtete Spur in {len(frames)} Aufnahmen über {span:.0f} s mit gleichmäßigem Fortschritt – ein Meteor leuchtet nur in einer Aufnahme.')
        for c in chain:
            detection=c['detection']
            detection.setdefault('single_frame_kind',detection.get('kind'))
            detection.update(track_id=f'L{count}',kind='plane',label=f'Bewegtes Objekt in {len(frames)} Aufnahmen (Satellit/Flugzeug?)',linked_track=info)
    return count
