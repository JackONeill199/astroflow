"""Schnellere Abschnittssuche gegen die bisherige erschöpfende Rechnung prüfen.

Ohne pytest auch im separaten Engine-Interpreter ausführbar: python tests/test_skytrace_performance.py
"""
from pathlib import Path
import sys

import numpy as np

if __name__ != '__main__':
    import pytest
    pytest.importorskip('cv2')

import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'astroflow/modules/skytrace/engine'))
import line_detection as lines


def reference_section(bins):
    good=bins>.32
    best=None
    for start in np.where(good)[0]:
        for end in range(start+2,len(bins)+1):
            section=good[start:end]
            if section.mean()<.65 or not section[-1]: continue
            strength=float(np.mean(bins[start:end])*np.sqrt((end-start)*24/4))
            if strength<lines.required_score(end-start): continue
            if best is None or strength>best[0]: best=(strength,start,end)
    return best


def test_sections_match_exhaustive_search():
    rng=np.random.default_rng(2026)
    for n in (0,1,2,3,7,16,32,64,128,129):
        cases=[rng.uniform(-4,4,n) for _ in range(12)]
        cases.extend([np.zeros(n),np.full(n,.32),np.full(n,4.)])
        if n>=2:
            boundary=lines.required_score(n)/np.sqrt(n*24/4)
            cases.extend(np.full(n,v) for v in (boundary,np.nextafter(boundary,-np.inf),np.nextafter(boundary,np.inf)))
            cases.append(np.resize([4.,4.,0.,0.,4.,4.,4.],n))  # Lücken und gleich starke Abschnitte
        for bins in cases:
            assert lines._best_section(bins)==reference_section(bins),(n,bins)


def test_detection_matches_exhaustive_search():
    rng=np.random.default_rng(93)
    base=rng.normal(1000,15,(480,640)).astype(np.float32)
    for x,y in rng.integers(20,460,(60,2)):
        cv2.circle(base,(int(x),int(y)),2,2000,-1)
    base=cv2.GaussianBlur(base,(0,0),.9)
    optimized=lines._best_section
    cases=[None,((40,100),(580,390)),((40,200),(580,200)),((320,30),(320,450)),((210,200),(310,270))]
    try:
        for segment in cases:
            image=base.copy()
            if segment: cv2.line(image,*segment,1300,2)
            for comparison in (None,base):
                lines._best_section=reference_section
                expected=lines.detect_lines(image,comparison,np.eye(2,3))
                lines._best_section=optimized
                actual=lines.detect_lines(image,comparison,np.eye(2,3))
                assert actual==expected,segment
    finally:
        lines._best_section=optimized


if __name__=='__main__':
    test_sections_match_exhaustive_search()
    test_detection_matches_exhaustive_search()
    print('OK: Abschnittsgrenzen, Grenzwerte, Gleichstände und vollständige Spurerkennung unverändert')
