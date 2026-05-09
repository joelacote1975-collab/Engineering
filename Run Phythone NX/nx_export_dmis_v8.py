# ==============================================================================
# NX Open Python Script
# Name    : nx_export_dmis_v8.py
# Fix v8  : Truncate/dedup 8-char DMIS label; fix record length 80 chars;
#           fix comment line to DMIS $$ format
# Fix v8c : Replace uf.Modl (not available in this NX) with uf.Evalsf:
#             Initialize(face_tag) → FindClosestPoint(ev, pt) → Evaluate(1, uv)
#             Normal = SrfDu × SrfDv (cross product), then normalize
#           Remove all grid-search / AskFaceData / AskFaceProps code
# ==============================================================================

import NXOpen
import NXOpen.UF
import NXOpen.Features
import math
import os

OUTPUT_PATH = r"D:\CMM_Data\DMIS_export.txt"

SKIP_TYPES = (
    "DATUM_PLANE", "DATUM PLANE", "ABSOLUTE_DATUM_PLANE",
    "DATUM_AXIS", "DATUM AXIS",
    "DATUM_CSYS", "DATUM CSYS", "DATUM_COORDINATE_SYSTEM",
    "BREP", "BODY",
    "SKETCH", "CONSTRAINT",
    "BOOLEAN", "EXTRUDE", "REVOLVE", "SWEEP",
    "FILLET", "CHAMFER", "BLEND",
    "TRIM_BODY", "SHELL", "MIRROR_BODY", "PATTERN",
    "THROUGH_CURVE", "THROUGH_CURVE_MESH", "SECTION",
)


def build_face_tags(work_part):
    """Pre-build face tag list once — reused for every point lookup."""
    tags = []
    for body in work_part.Bodies:
        try:
            for face in body.GetFaces():
                tags.append(face.Tag)
        except Exception:
            continue
    return tags


def get_normal_for_point(uf, face_tags, px, py, pz, lw):
    """
    วนทุก Face (จาก pre-built list) หาจุดที่ใกล้ (px,py,pz) ที่สุด
    แล้วคำนวณ unit normal = SrfDu × SrfDv ที่จุดนั้น

    API: uf.Evalsf.Initialize  → evaluator
         uf.Evalsf.FindClosestPoint(ev, pt) → .Distance, .Uv
         uf.Evalsf.Evaluate(ev, 1, uv)     → .SrfDu, .SrfDv
    """
    best_normal = (0.0, 0.0, 1.0)
    best_dist   = float('inf')

    for face_tag in face_tags:
        ev = None
        try:
            ev = uf.Evalsf.Initialize(face_tag)
            cp = uf.Evalsf.FindClosestPoint(ev, [px, py, pz])
            d  = cp.Distance

            if d < best_dist:
                srf = uf.Evalsf.Evaluate(ev, 1, list(cp.Uv))
                du  = srf.SrfDu
                dv  = srf.SrfDv
                # normal = du × dv
                nx  = du[1]*dv[2] - du[2]*dv[1]
                ny  = du[2]*dv[0] - du[0]*dv[2]
                nz  = du[0]*dv[1] - du[1]*dv[0]
                ln  = math.sqrt(nx*nx + ny*ny + nz*nz)
                if ln > 1e-10:
                    best_dist   = d
                    best_normal = (nx/ln, ny/ln, nz/ln)

        except Exception:
            pass
        finally:
            # Free evaluator to prevent memory leak
            if ev is not None:
                try:
                    uf.Evalsf.Free(ev)
                except Exception:
                    pass

    lw.WriteFullline(
        f"       [searched {len(face_tags)} faces — nearest dist={best_dist:.4f}]"
    )
    return best_normal


def extract_xyz(feat):
    """ดึง XYZ จาก Feature (Point / CPROJ)"""
    try:
        for ent in feat.GetEntities():
            try:
                c = ent.Coordinates
                return (c.X, c.Y, c.Z)
            except Exception:
                pass
            try:
                pt = NXOpen.Point(ent.Tag)
                return (pt.Coordinates.X, pt.Coordinates.Y, pt.Coordinates.Z)
            except Exception:
                pass
            try:
                crv = NXOpen.Curve(ent.Tag)
                sp  = crv.StartPoint
                return (sp.X, sp.Y, sp.Z)
            except Exception:
                pass
    except Exception:
        pass

    try:
        uf = NXOpen.UF.UFSession.GetUFSession()
        for ent in feat.GetEntities():
            try:
                pt_data = uf.Curve.AskPointData(ent.Tag)
                return (pt_data[0], pt_data[1], pt_data[2])
            except Exception:
                pass
    except Exception:
        pass

    return None


def dmis_label(name):
    """DMIS label = max 8 chars, left-justified"""
    return f"{name[:8]:<8s}"


def fmt_coord(val, width=9, dec=3):
    """Format coordinate into fixed-width field; reduce decimals if value too large."""
    for d in range(dec, -1, -1):
        s = f"{val:{width}.{d}f}"
        if len(s) <= width:
            return s
    # Last resort: integer truncated to width
    return f"{int(val)}"[:width].ljust(width)


def main():
    session = NXOpen.Session.GetSession()
    lw      = session.ListingWindow
    lw.Open()

    lw.WriteFullline("=" * 68)
    lw.WriteFullline("  NX Export DMIS v8c — Quindos MDI Export")
    lw.WriteFullline("=" * 68)

    work_part = session.Parts.Work
    if work_part is None:
        lw.WriteFullline("ERROR: No active part.")
        return

    part_name = os.path.splitext(os.path.basename(work_part.FullPath))[0]
    lw.WriteFullline(f"  Part   : {part_name}")
    lw.WriteFullline(f"  Output : {OUTPUT_PATH}")

    uf = NXOpen.UF.UFSession.GetUFSession()

    face_tags    = build_face_tags(work_part)
    total_bodies = sum(1 for _ in work_part.Bodies)
    total_faces  = len(face_tags)
    lw.WriteFullline(f"  Model  : {total_bodies} bodies, {total_faces} faces")
    lw.WriteFullline("")
    lw.WriteFullline(f"  {'#':<4} {'Name':<20} {'Type':<30} Action")
    lw.WriteFullline("  " + "-" * 70)

    points = []
    seen   = set()   # dedup บน 8-char label

    for idx, feat in enumerate(work_part.Features):
        ft       = feat.FeatureType.upper().strip()
        raw_name = feat.Name.strip()

        if not raw_name:
            lw.WriteFullline(
                f"  {idx:<4} {'(unnamed)':<20} {ft[:29]:<30} SKIP"
            )
            continue

        if any(ft.startswith(s) for s in SKIP_TYPES):
            lw.WriteFullline(
                f"  {idx:<4} {raw_name[:19]:<20} {ft[:29]:<30} SKIP"
            )
            continue

        lw.WriteFullline(
            f"  {idx:<4} {raw_name[:19]:<20} {ft[:29]:<30} Processing"
        )

        xyz = extract_xyz(feat)
        if xyz is None:
            lw.WriteFullline("       → No coordinates found — skip")
            continue

        px, py, pz = xyz

        # Unique 8-char DMIS label
        base  = raw_name[:8]
        label = base
        suf   = 1
        while label in seen:
            s     = str(suf)
            label = base[:8 - len(s)] + s
            suf  += 1
        seen.add(label)

        lw.WriteFullline(f"       → XYZ=({px:.3f}, {py:.3f}, {pz:.3f})")
        uvw = get_normal_for_point(uf, face_tags, px, py, pz, lw)
        lw.WriteFullline(
            f"       → UVW=({uvw[0]:.4f}, {uvw[1]:.4f}, {uvw[2]:.4f})"
        )

        points.append({
            'label': label,
            'x': px, 'y': py, 'z': pz,
            'u': uvw[0], 'v': uvw[1], 'w': uvw[2],
        })

    # ── Write DMIS file ───────────────────────────────────────────────────────
    lw.WriteFullline("")
    if not points:
        lw.WriteFullline("ERROR: No points exported.")
        return

    # DMIS record = 80 chars (ไม่นับ newline)
    # LABEL(8) + COMMAND + padding + CHECKSUM(8) = 80
    try:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:

            # Header  8+9+55+8 = 80
            f.write(f"{'POINTS':<8s}=HEADER/1{'':55s}00000000\n")

            # Comment  8+64+8 = 80
            comment = f"PUNKTE ERZEUGT AUS NX FUER QUINDOS - {part_name}"
            f.write(f"{'$$':<8s}{comment:<64s}00000000\n")

            # Data  8+8+56+8 = 80
            # nums = x(9)+,+y(9)+,+z(9)+,+u(8)+,+v(8)+,+w(8) = 56 chars
            for i, pt in enumerate(points, start=1):
                nc   = dmis_label(pt['label'])
                nums = (
                    f"{fmt_coord(pt['x'],9,3)},{fmt_coord(pt['y'],9,3)},{fmt_coord(pt['z'],9,3)},"
                    f"{fmt_coord(pt['u'],8,4)},{fmt_coord(pt['v'],8,4)},{fmt_coord(pt['w'],8,4)}"
                )
                f.write(f"{nc}=MDI/1, {nums}{i*10:08d}\n")

            # End  8+4+60+8 = 80
            f.write(f"{'POINTS':<8s}=END{'':60s}00000000\n")

        lw.WriteFullline(f"  Exported {len(points)} points:")
        for pt in points:
            lw.WriteFullline(
                f"    {pt['label']:<10} "
                f"({pt['x']:8.3f},{pt['y']:8.3f},{pt['z']:8.3f}) "
                f"N=({pt['u']:7.4f},{pt['v']:7.4f},{pt['w']:7.4f})"
            )
        lw.WriteFullline(f"\n  File: {OUTPUT_PATH}")
        lw.WriteFullline("=" * 68)
        lw.WriteFullline(f"  Quindos: CNVVDA (FIL={OUTPUT_PATH})")
        lw.WriteFullline("=" * 68)

    except Exception as e:
        lw.WriteFullline(f"ERROR writing file: {e}")


if __name__ == '__main__':
    main()
