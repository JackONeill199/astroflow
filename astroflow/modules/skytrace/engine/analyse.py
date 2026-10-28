"""Local, read-only astronomy analysis. JSON lines are the live UI protocol."""
import argparse
import collections
import csv
import hashlib
import html
import json
import os
import re
from pathlib import Path
import time

from project_paths import ASSETS, BASE, CODE
PHOTO = {'.fits', '.fit', '.fts', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.cr2', '.cr3', '.nef', '.arw', '.dng'}
VIDEO = {'.mp4', '.avi', '.mov', '.mkv', '.m4v'}


def event(kind, **data):
    print(json.dumps(dict(type=kind, **data), ensure_ascii=True, allow_nan=False), flush=True)


def load_image(path):
    import numpy as np
    from PIL import Image
    meta = {}
    if path.suffix.lower() in {'.fits', '.fit', '.fts'}:
        from astropy.io import fits
        with fits.open(path, memmap=False) as hdus:
            hdu = next(h for h in hdus if h.data is not None and h.data.ndim >= 2)
            data = np.array(hdu.data, dtype=np.float32).squeeze()
            meta = {k: str(hdu.header[k]) for k in ['OBJECT', 'DATE-OBS', 'EXPTIME', 'EXPOSURE', 'GAIN', 'TEMP', 'INSTRUME', 'BAYERPAT', 'RA', 'DEC', 'NAXIS1', 'NAXIS2', 'FOCAL', 'PIXSZ'] if k in hdu.header}
            from motion_plausibility import exposure_info
            exposure=exposure_info(meta)
            if exposure['seconds'] is not None: meta['exposure_seconds']=exposure['seconds']
        if data.ndim == 3 and data.shape[0] in (1, 3, 4):
            data = np.moveaxis(data, 0, -1)
        # Bin a Bayer mosaic to suppress the CFA pattern; report scale explicitly.
        if data.ndim == 2 and meta.get('BAYERPAT'):
            h, w = data.shape
            data = data[:h//2*2, :w//2*2].reshape(h//2, 2, w//2, 2).mean((1, 3))
            meta['analysis_pixel_scale'] = '2 original pixels (Bayer 2x2 binning)'
    elif path.suffix.lower() in {'.cr2', '.cr3', '.nef', '.arw', '.dng'}:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            data = raw.postprocess(output_bps=16).astype(np.float32)
    else:
        with Image.open(path) as image:
            data = np.asarray(image).astype(np.float32)
    if data.ndim not in (2, 3):
        raise ValueError(f'Nicht unterstützte Bilddimension: {data.shape}')
    if data.ndim == 3:
        data = data[..., :3].mean(axis=2)
    if not np.isfinite(data).any():
        raise ValueError('Keine gültigen Pixel')
    if path.suffix.lower() in {'.fits','.fit','.fts'}:
        sidecar=path.with_suffix('.Info.txt')
        if sidecar.exists():
            try:
                text=sidecar.read_text(encoding='utf-8',errors='replace')
                coordinates=re.findall(r'^Center\s*=\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)',text,re.MULTILINE)
                factor=2 if meta.get('BAYERPAT') and 'analysis_pixel_scale' in meta else 1
                if coordinates or re.search(r'^NrStars\s*=',text,re.MULTILINE):
                    meta['_stellar_centroids']=[[float(x)/factor,float(y)/factor] for x,y in coordinates]
            except (OSError,ValueError): pass
    return np.nan_to_num(data), meta


def measure(data):
    import numpy as np
    from scipy.ndimage import gaussian_filter, maximum_filter
    # Metrics are measured at native analysis scale, never on the thumbnail.
    background = gaussian_filter(data, 12)
    residual = data - background
    med = float(np.median(residual))
    noise = float(1.4826 * np.median(np.abs(residual - med)))
    smooth = gaussian_filter(residual, 1)
    mask = (smooth == maximum_filter(smooth, 9)) & (smooth > med + 6 * max(noise, 1e-6))
    mask[:9] = mask[-9:] = False
    mask[:, :9] = mask[:, -9:] = False
    ys, xs = np.where(mask)
    order = np.argsort(smooth[ys, xs])[::-1][:250]
    widths, ellipses = [], []
    yy, xx = np.mgrid[-5:6, -5:6]
    for idx in order:
        y, x = ys[idx], xs[idx]
        patch = np.maximum(residual[y-5:y+6, x-5:x+6] - 2*noise, 0)
        flux = patch.sum()
        if flux <= 0:
            continue
        cx, cy = (patch*xx).sum()/flux, (patch*yy).sum()/flux
        vx = float((patch*(xx-cx)**2).sum()/flux)
        vy = float((patch*(yy-cy)**2).sum()/flux)
        cov = float((patch*(xx-cx)*(yy-cy)).sum()/flux)
        eig = np.linalg.eigvalsh([[vx, cov], [cov, vy]])
        if eig[0] > .15 and eig[1] < 16:
            widths.append(2.355 * np.sqrt(eig.mean()))
            ellipses.append(1 - np.sqrt(eig[0]/eig[1]))
    return dict(stars=int(len(xs)), fwhm=round(float(np.median(widths)), 2) if widths else None,
                elongation=round(float(np.median(ellipses)), 3) if ellipses else None,
                background=round(float(np.median(data)), 3), noise=round(noise, 3),
                width=int(data.shape[1]), height=int(data.shape[0]))


def preview(data, dest):
    import numpy as np
    from PIL import Image
    low, high = np.percentile(data[::3, ::3], [1, 99.8])
    scaled = np.clip((data-low)/max(high-low, 1e-6), 0, 1)
    image = Image.fromarray((np.arcsinh(scaled*12)/np.arcsinh(12)*255).astype('uint8'))
    image.thumbnail((920, 620))
    image.save(dest)


def session(path, root):
    if '_observation_' in root.name:
        return root.name
    rel = path.relative_to(root)
    return rel.parts[0] if len(rel.parts) > 1 else 'Einzelbilder'


def write_report(out, rows, inventory):
    def esc(value):
        return html.escape(str(value if value is not None else '–'))
    cards = []
    for row in rows:
        label = ' / '.join(str(row.get(k, '–')) for k in ['stars', 'fwhm', 'noise'])
        cards.append(f'<article><img loading="lazy" src="{esc(row["preview"])}"><div><h2>{esc(row["session"])}</h2><p>{esc(Path(row["file"]).name)}</p><p>Sternkandidaten / FWHM (px) / Hintergrundrauschen: {esc(label)}</p><p>{esc(row.get("meta", {}))}</p><p>{esc(row.get("note", ""))}</p><a href="{esc(Path(row["file"]).as_uri())}">Original öffnen</a></div></article>')
    page = '<!doctype html><html lang="de"><meta charset="utf-8"><title>Stellina · Analyse</title><style>body{background:#0e1728;color:#e7eef9;font:16px Segoe UI,sans-serif;margin:40px}h1{font-size:36px}p{line-height:1.6;color:#b9c9df}article{display:flex;gap:28px;background:#19263c;padding:20px;margin:24px 0;border-radius:16px}img{width:42%;object-fit:contain}a{color:#66d6d0}h2{font-size:18px;overflow-wrap:anywhere}@media(max-width:800px){article{display:block}img{width:100%}}</style>'
    page += f'<h1>Stellina · Lokale Analyse</h1><p>{len(rows)} analysierte Aufnahmen · Bestand: {esc(inventory)}</p><p>Die Objektbezeichnungen stammen aus Ordnernamen/FITS-Metadaten; keine unabhängige Objekterkennung oder Astrometrie. FWHM ist eine grobe Schätzung aus Sternmomenten, keine Fokuskalibrierung. Sternkandidaten können Hotpixel und Nebelstrukturen enthalten. Werte nur innerhalb gleicher Aufnahmeart und Skalierung vergleichen. Für Mond/Planeten sind Sternmesswerte nicht geeignet. FITS-Vorschauen sind automatisch gestreckt; Bayer-Bilder werden 2×2 gebinnt.</p>'
    page += ''.join(cards)
    temp = out/'bericht.tmp'
    temp.write_text(page, encoding='utf-8')
    temp.replace(out/'bericht.html')


def run(args):
    root, out = Path(args.source).resolve(), Path(args.output).resolve()
    if not root.is_dir():
        raise ValueError('Quellordner ist nicht vorhanden')
    if out == root or root in out.parents:
        raise ValueError('Ergebnisordner muss außerhalb der Quelldaten liegen')
    out.mkdir(parents=True, exist_ok=True)
    (out/'vorschau').mkdir(exist_ok=True)
    files, inventory = [], collections.Counter()
    event('status', text='Dateibestand und Beobachtungsordner werden eingelesen …')
    for folder, _, names in os.walk(root):
        if (out/'STOP').exists():
            return
        for name in sorted(names):
            p = Path(folder)/name
            inventory[p.suffix.lower()] += 1
            if p.suffix.lower() in (PHOTO if args.mode == 'Fotos' else VIDEO):
                files.append(p)
    (out/'bestand.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    if args.mode == 'Fotos':
        grouped = collections.defaultdict(list)
        for p in files:
            grouped[session(p, root)].append(p)
        chosen = []
        for name, group in sorted(grouped.items()):
            fits_files = [p for p in group if p.suffix.lower() in {'.fits', '.fit', '.fts'}]
            group = sorted(fits_files or group)
            if args.samples:
                n = min(args.samples, len(group))
                group = [group[round(i*(len(group)-1)/max(n-1, 1))] for i in range(n)]
            chosen.extend(group)
        files = chosen
    if args.limit:
        files = files[:args.limit]
    event('inventory', total=len(files), text=f'{sum(inventory.values())} Dateien; {len(files)} zur Analyse ausgewählt')
    rows = []
    model = None
    if args.mode != 'Fotos' and files:
        os.environ['YOLO_CONFIG_DIR'] = str(BASE/'runtime')
        from ultralytics import YOLO
        model = YOLO(str(ASSETS/'models/night-detector/night-yolo26s-1920.pt'))
    with (out/'messwerte.jsonl').open('w', encoding='utf-8') as stream:
        for i, path in enumerate(files, 1):
            if (out/'STOP').exists():
                break
            event('status', text=f'{session(path, root)} · {path.name}', current=i, total=len(files))
            try:
                if model is None:
                    data, meta = load_image(path)
                    solar = any(token in session(path, root).lower() for token in ['_moon', '_saturn', '_mars', '_uranus', '_neptune', '_jupiter', '_venus', '_mercury', '_sun'])
                    metrics = measure(data) if not solar else dict(stars=None, fwhm=None, elongation=None, background=None, noise=None, width=int(data.shape[1]), height=int(data.shape[0]))
                    thumb = 'vorschau/'+hashlib.sha256(str(path).encode()).hexdigest()[:20]+'.png'
                    preview(data, out/thumb)
                    clean_thumb=str(Path(thumb).with_name(Path(thumb).stem+'-clean.png'))
                    import shutil
                    shutil.copyfile(out/thumb,out/clean_thumb)
                    if args.solve and not solar:
                        from solving import solve_image, objects_for_frame
                        from sequence import annotate
                        import cv2
                        solve_folder=out/'plates'/hashlib.sha256(str(path).encode()).hexdigest()[:20]
                        solve_folder.mkdir(parents=True,exist_ok=True)
                        solved=solve_image(path,data,solve_folder,out)
                        if solved:
                            gray=cv2.imread(str(out/thumb),cv2.IMREAD_GRAYSCALE)
                            objects=objects_for_frame(solved,gray.shape)
                            drawn=annotate(gray,[],session(path,root),meta.get('DATE-OBS',''),0,1,False,1,objects)
                            cv2.imwrite(str(out/thumb),drawn)
                            meta['plate_solution']=dict(ra=solved['ra'],dec=solved['dec'],objects=objects)
                    row = dict(file=str(path), session=session(path, root), preview=thumb, meta=meta, note='Mond/Planet: Sternmessung übersprungen.' if solar else '', **metrics)
                    rows.append(row)
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                    stream.flush()
                    write_report(out, rows, dict(inventory))
                    event('result', **row, preview_absolute=str(out/thumb), preview_clean_absolute=str(out/clean_thumb),current=i, total=len(files))
                else:
                    video(path, root, out, model, stream, args.stride, i, len(files), rows)
                    write_report(out, rows, dict(inventory))
            except Exception as error:
                event('error', text=f'{path}: {error}')
    write_report(out, rows, dict(inventory))
    event('done', text='Gestoppt – Teilergebnisse gespeichert.' if (out/'STOP').exists() else ('Fertig.' if files else 'Keine passenden Dateien gefunden.'), report=str(out/'bericht.html'))


def video(path, root, out, model, stream, stride, current, total, rows):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError('Video kann nicht geöffnet werden')
    fps = cap.get(cv2.CAP_PROP_FPS)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_no = 0
    try:
        while not (out/'STOP').exists():
            ok, frame = cap.read()
            if not ok:
                break
            if frame_no % stride == 0:
                h, w = frame.shape[:2]
                scaled = cv2.resize(frame, (round(w*min(1, 1920/max(h,w))), round(h*min(1, 1920/max(h,w)))))
                result = model.predict(scaled, imgsz=1920, conf=.3, device='cpu', verbose=False)[0]
                detections = [dict(label=model.names[int(b.cls.item())], confidence=round(float(b.conf.item()), 4), box=b.xyxyn.tolist()[0]) for b in result.boxes]
                row = dict(file=str(path), frame=frame_no, seconds=round(frame_no/fps, 3) if fps else None, detections=detections)
                if detections:
                    stream.write(json.dumps(row)+'\n'); stream.flush()
                    thumb = f'vorschau/hit-{current}-{frame_no}.jpg'
                    cv2.imwrite(str(out/thumb), result.plot())
                    rows.append(dict(file=str(path), session=session(path, root), preview=thumb, meta=dict(seconds=row['seconds'], detections=detections), note='OpenSkyCam-Modellkandidaten; Sichtprüfung erforderlich.'))
                    write_report(out, rows, {})
                if frame_no % (stride*5) == 0 or detections or frame_no==count-1:
                    from image_store import write_image
                    live = out/'vorschau'/f'live-{frame_no%2}.jpg'
                    write_image(live,result.plot())
                    clean=live.with_name(live.stem+'-clean.jpg');write_image(clean,scaled)
                    event('video', preview_absolute=str(live),preview_clean_absolute=str(clean),frame_total=count,text=f'{path.name} · Frame {frame_no+1}/{count} · {len(detections)} Kandidaten', **row, current=current, total=total)
            frame_no += 1
    finally:
        cap.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', default='Fotos')
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--stride', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--fps', type=float, default=8)
    parser.add_argument('--solve', action='store_true')
    args = parser.parse_args()
    if args.samples < 0 or args.stride < 1 or not 1 <= args.fps <= 60:
        parser.error('samples >= 0, stride >= 1 und fps zwischen 1 und 60 erforderlich')
    try:
        if args.mode == 'Bildfolge → Ereignisvideo':
            from sequence import run_sequences
            run_sequences(args)
        else:
            run(args)
    except Exception as error:
        event('fatal', text=str(error))
        raise
