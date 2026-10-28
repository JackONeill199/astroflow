"""Explain rejected frame/detection evidence without erasing the original analysis."""
import html
from pathlib import Path


def render(out,folder,summary,removed):
    sections=[]
    for row in removed:
        quality=row.get('quality',{})
        image=folder/f'bewegung-ausgeschlossen-{row["index"]:06d}.jpg'
        photo=f'<a href="{html.escape(image.relative_to(out).as_posix())}"><img src="{html.escape(image.relative_to(out).as_posix())}" alt="Unbeschriftete Originalaufnahme, Bild {row["index"]+1}"></a>' if image.exists() else ''
        facts=[('Originalbild',str(row['index']+1)),('Erkennungen im ursprünglichen Lauf',str(len(row.get('detections',[])))),
               ('Untersuchte Sterne',str(quality.get('tail_reference_stars','nicht gemessen'))),
               ('Sterne mit langen schwachen Ausläufern',str(quality.get('tail_stars','nicht gemessen'))),
               ('Richtungskohärenz der Ausläufer',str(quality.get('tail_direction_coherence','nicht gemessen'))),
               ('Bildquadranten mit Ausläufern',str(quality.get('tail_quadrants','nicht gemessen'))),
               ('Median der Ausläuferlänge [Analysepx]',str(quality.get('tail_extent_pixels','nicht gemessen')))]
        table=''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(v)+'</td></tr>' for k,v in facts)
        sections.append('<section><h2>Bild '+str(row['index']+1)+'</h2><p>'+html.escape(row['reason'])+'</p><table>'+table+'</table>'+photo+'</section>')
    page='''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bewegungsprüfung · AstroAnalyse</title><style>body{background:#0c1424;color:#e8effc;font:16px system-ui;max-width:1250px;margin:30px auto;padding:0 20px}section{padding:24px;background:#17243a;margin:22px 0;border-radius:12px}img{width:100%;height:auto;margin-top:20px}table{border-collapse:collapse}th,td{text-align:left;padding:8px 20px 8px 0;border-bottom:1px solid #ffffff20}p{line-height:1.7}a{color:#70dfd0}</style><h1>Bewegungsprüfung</h1><p>Dieser eigenständige Nachprüfungslauf erhält den ursprünglichen Lauf und alle Quelldateien. Viele Erkennungen im selben Bild sind allein kein Ausschlussgrund. Entscheidend sind unabhängige gemeinsame Sternschweife über das Bildfeld bzw. eine belastbar gemessene Krümmung einer vorgeschlagenen Spur. Eine Sternregistrierung zwischen Bildern korrigiert keine Verwacklung während einer einzelnen Belichtung.</p><p><a href="bericht.html">Zum korrigierten Ergebnisbericht</a></p>'''+''.join(sections)+'''<p>Die neue Schärfeprüfung umfasst auch schwache Ausläufer außerhalb der hellen Sternkerne. Formprüfungen verwerfen nur ausreichend vermessene, deutlich gekrümmte Modellvorschläge. Bereits geometrisch integrierte gerade Spuren werden nicht allein wegen gleichzeitiger anderer Erkennungen entfernt.</p></html>'''
    (out/'bewegungspruefung.html').write_text(page,encoding='utf-8')
