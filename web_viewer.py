#!/usr/bin/env python3
"""
SQLite 数据 Web 查看器
用法：python web_viewer.py --db D:/data/cl_db/cl.db
然后浏览器打开 http://localhost:5000

磁力链接依赖 pip install torrentool（解析本地 .torrent）。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    request,
    g,
    render_template_string,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

try:
    from torrentool.api import Torrent as _TorrentoolTorrent
except ImportError:
    _TorrentoolTorrent = None

app = Flask(__name__)
DB_PATH: Path = Path("cl.db")
DEFAULT_ITEMS_SOURCE = (
    "zz"  # 番号库「来源」无查询参数时的默认值（/?source= 为空表示全部来源）
)


# ── 数据库连接 ──────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db:
        db.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _bytes_to_gb_str(n: int) -> str:
    """文件字节数转为 GB 展示字符串（与番号库 item-size 风格相近）。"""
    gb = n / (1024**3)
    text = f"{gb:.2f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _path_has_picpic_dir(path: str) -> bool:
    """path 路径分段中是否包含目录名 PICPIC（大小写不敏感）。"""
    if not path:
        return False
    parts = re.split(r"[/\\]+", path)
    return any(p.casefold() == "picpic" for p in parts if p)


def _path_has_temp_dir(path: str) -> bool:
    """path 路径分段中是否包含目录名 temp（大小写不敏感）。"""
    if not path:
        return False
    parts = re.split(r"[/\\]+", path)
    return any(p.casefold() == "temp" for p in parts if p)


def _row_title_transfer(row: sqlite3.Row) -> str | None:
    """兼容未迁移库：无 title_transfer 列时返回 None。"""
    if "title_transfer" not in row.keys():
        return None
    v = row["title_transfer"]
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _item_list_title(row: sqlite3.Row) -> str:
    """列表第二行标题：优先 title_transfer，否则 code_title。"""
    tt = _row_title_transfer(row)
    if tt:
        return tt
    return (row["code_title"] or "").strip() or "—"


def _local_hints_for_codes(conn: sqlite3.Connection, codes: list[str]) -> dict[str, dict]:
    """code -> {picpic, video_temp, video_id, video_size_gb}；多条视频取最小 id。"""
    if not codes or not _table_exists(conn, "local_items"):
        return {}
    uniq = sorted({c for c in codes if c})
    if not uniq:
        return {}
    ph = ",".join("?" * len(uniq))
    sql = f"SELECT id, code, path, file_type, root, size FROM local_items WHERE code IN ({ph})"
    hints: dict[str, dict] = {}
    for row in conn.execute(sql, uniq):
        c = row["code"]
        if c not in hints:
            hints[c] = {
                "picpic": False,
                "video_temp": False,
                "video_id": None,
                "video_size_gb": None,
            }
        if _path_has_picpic_dir(row["path"]):
            hints[c]["picpic"] = True
        if (row["file_type"] or "") == "video":
            vid = int(row["id"])
            cur = hints[c]["video_id"]
            if cur is None or vid < cur:
                hints[c]["video_id"] = vid
                hints[c]["video_size_gb"] = _bytes_to_gb_str(int(row["size"]))
                hints[c]["video_temp"] = _path_has_temp_dir(row["path"])
    return hints


def _open_local_media(path: Path) -> None:
    s = os.fspath(path)
    if sys.platform == "win32":
        os.startfile(s)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", s], check=False)
    else:
        subprocess.run(["xdg-open", s], check=False)


def _validate_local_video_path(resolved: Path, root_letter: str | None) -> bool:
    if not resolved.is_file():
        return False
    letter = (root_letter or "").strip().upper().rstrip(":")
    if not letter or sys.platform != "win32":
        return True
    return resolved.drive.casefold() == f"{letter}:".casefold()


# ── 路由 ────────────────────────────────────────────────────
@app.route("/")
def index():
    db = get_db()
    stats = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM threads)              AS total_threads,
            (SELECT COUNT(*) FROM threads WHERE status='done') AS done_threads,
            (SELECT COUNT(*) FROM items)                AS total_items,
            (SELECT COUNT(DISTINCT actress) FROM items WHERE actress IS NOT NULL AND actress != '') AS total_actresses,
            (SELECT COUNT(DISTINCT source) FROM items)  AS total_sources
    """
    ).fetchone()
    sources = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT source FROM items WHERE source != '' ORDER BY source"
        ).fetchall()
    ]
    return render_template_string(TEMPLATE_INDEX, stats=stats, sources=sources)


def _send_local_file(
    path_str: str | None,
    *,
    attachment: bool,
    fallback_name: str,
    mimetype: str | None = None,
    download_name: str | None = None,
):
    """按数据库中的绝对路径下发本地文件；不存在则 404。"""
    if not path_str or not str(path_str).strip():
        abort(404)
    p = Path(path_str)
    try:
        p = p.expanduser().resolve(strict=False)
    except OSError:
        abort(404)
    if not p.is_file():
        abort(404)
    dl_name = download_name or (p.name if p.name else fallback_name)
    if attachment:
        return send_file(
            p,
            mimetype=mimetype,
            as_attachment=True,
            download_name=dl_name,
            max_age=0,
        )
    return send_file(
        p,
        mimetype=mimetype,
        as_attachment=False,
        max_age=86400,
    )


_TORRENT_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _torrent_url_slug(code: str | None) -> str:
    """URL 路径片段（不含 .torrent / .torrent.html）；secure_filename 为空时退回净化后的原始番号。"""
    raw = str(code or "").strip()
    s = secure_filename(raw)
    if s:
        return s[:180]
    cleaned = _TORRENT_FILENAME_BAD.sub("_", raw).strip("._")[:180]
    return cleaned or "torrent"


app.jinja_env.globals["torrent_slug"] = _torrent_url_slug


def _torrent_download_filename(path: Path, code: str | None) -> str:
    """保证附件名为 .torrent，避免客户端按「文本」保存成 .txt。"""
    if path.suffix.lower() == ".torrent" and path.name:
        return path.name
    stem = (code or "").strip()
    stem = _TORRENT_FILENAME_BAD.sub("_", stem).strip("._")[:200]
    if not stem:
        stem = path.stem.strip("._") or "torrent"
        stem = _TORRENT_FILENAME_BAD.sub("_", stem)[:200] or "torrent"
    return f"{stem}.torrent"


def _item_local_torrent_path(item_id: int) -> Path | None:
    """本地种子绝对路径；无记录、路径无效或文件不存在则 None。"""
    db = get_db()
    row = db.execute("SELECT torrent_path FROM items WHERE id = ?", (item_id,)).fetchone()
    path_str = row["torrent_path"] if row else None
    if not path_str or not str(path_str).strip():
        return None
    p = Path(path_str).expanduser()
    try:
        p = p.resolve(strict=False)
    except OSError:
        return None
    if not p.is_file():
        return None
    return p


def _resolve_item_torrent(item_id: int, torrent_name: str) -> tuple[Path, str]:
    """URL 须以 .torrent.html 结尾；正文仍从数据库 torrent_path 指向的 *.torrent 文件读取。"""
    if not torrent_name.lower().endswith(".torrent.html"):
        abort(404)
    p = _item_local_torrent_path(item_id)
    if p is None:
        abort(404)
    db = get_db()
    row = db.execute("SELECT code FROM items WHERE id = ?", (item_id,)).fetchone()
    code = row["code"] if row else None
    fname = _torrent_download_filename(p, code)
    return p, fname


@app.route("/files/item/<int:item_id>/image")
def item_image_file(item_id: int):
    db = get_db()
    row = db.execute("SELECT img_path FROM items WHERE id = ?", (item_id,)).fetchone()
    path_str = row["img_path"] if row else None
    return _send_local_file(path_str, attachment=False, fallback_name="image")


@app.route("/files/item/<int:item_id>/torrent", methods=["GET", "HEAD"])
def item_torrent_legacy(item_id: int):
    """旧链接 /torrent；重定向到 *.torrent.html。"""
    db = get_db()
    row = db.execute("SELECT code FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        abort(404)
    slug = _torrent_url_slug(row["code"])
    return redirect(f"/files/item/{item_id}/{slug}.torrent.html", code=302)


@app.route("/files/item/<int:item_id>/magnet")
def item_magnet_link(item_id: int):
    """从本地种子解析磁力链接；默认 302 跳转 magnet:?；?format=json 返回 JSON。"""
    if _TorrentoolTorrent is None:
        abort(
            503,
            description="未安装 torrentool，请执行: pip install torrentool",
        )
    p = _item_local_torrent_path(item_id)
    if p is None:
        abort(404)
    try:
        tor = _TorrentoolTorrent.from_file(os.fspath(p))
        link = (tor.magnet_link or "").strip()
        print(link)
    except Exception:
        abort(422)
    if not link:
        abort(422)
    if request.args.get("format") == "json":
        return jsonify(magnet_link=link)
    return redirect(link, code=302)


@app.route("/files/item/<int:item_id>/<path:torrent_name>", methods=["GET", "HEAD"])
def item_torrent_file(item_id: int, torrent_name: str):
    """URL 形如 …/番号.torrent.html；读取磁盘上 torrent_path 对应种子字节作为响应正文。"""
    tl = torrent_name.lower()
    if tl.endswith(".torrent") and not tl.endswith(".torrent.html"):
        db = get_db()
        row = db.execute("SELECT code FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            abort(404)
        slug = _torrent_url_slug(row["code"])
        return redirect(f"/files/item/{item_id}/{slug}.torrent.html", code=301)

    p, _ = _resolve_item_torrent(item_id, torrent_name)
    ct_html = "text/html; charset=UTF-8"
    if request.method == "HEAD":
        try:
            p.stat()
        except OSError:
            abort(404)
        resp = make_response("", 200)
        resp.headers["Content-Type"] = ct_html
        resp.headers.pop("Content-Length", None)
        resp.headers.pop("Content-Disposition", None)
        if getattr(resp, "content_length", None) is not None:
            resp.content_length = None
        return resp
    try:
        body = p.read_bytes()
    except OSError:
        abort(404)
    resp = make_response(body, 200)
    resp.headers["Content-Type"] = ct_html
    resp.headers.pop("Content-Length", None)
    resp.headers.pop("Content-Disposition", None)
    if getattr(resp, "content_length", None) is not None:
        resp.content_length = None
    return resp


@app.route("/local/play/<int:lid>")
def play_local_video(lid: int):
    """在本机用默认程序打开 local_items 中的视频（仅 Windows 校验盘符与 root 一致）。"""
    db = get_db()
    if not _table_exists(db, "local_items"):
        abort(404)
    row = db.execute(
        "SELECT path, root, file_type FROM local_items WHERE id = ?", (lid,)
    ).fetchone()
    if not row or (row["file_type"] or "") != "video":
        abort(404)
    try:
        p = Path(row["path"]).expanduser().resolve(strict=False)
    except OSError:
        abort(404)
    if not _validate_local_video_path(p, row["root"]):
        abort(403)
    try:
        _open_local_media(p)
    except OSError:
        abort(500)
    ref = request.referrer or ""
    origin = request.host_url.rstrip("/")
    if ref.startswith(origin):
        return redirect(ref)
    return redirect(url_for("items"))


@app.route("/items")
def items():
    db = get_db()
    q = request.args.get("q", "").strip()
    if "source" not in request.args:
        source = DEFAULT_ITEMS_SOURCE
    else:
        source = request.args.get("source", "").strip()
    actress = request.args.get("actress", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per = 90
    offset = (page - 1) * per

    conds, params = [], []
    if q:
        conds.append("(code LIKE ? OR code_title LIKE ? OR actress LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if source:
        conds.append("source = ?")
        params.append(source)
    if actress:
        conds.append("actress LIKE ?")
        params.append(f"%{actress}%")

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = db.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM items {where} ORDER BY crawled_at DESC LIMIT ? OFFSET ?",
        params + [per, offset],
    ).fetchall()

    sources = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT source FROM items WHERE source != '' ORDER BY source"
        ).fetchall()
    ]
    actresses = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT actress FROM items WHERE actress IS NOT NULL AND actress != '' ORDER BY actress LIMIT 200"
        ).fetchall()
    ]

    codes_on_page = [r["code"] for r in rows]
    local_hints = _local_hints_for_codes(db, codes_on_page)

    item_list_title = {r["id"]: _item_list_title(r) for r in rows}

    total_pages = max(1, (total + per - 1) // per)
    return render_template_string(
        TEMPLATE_ITEMS,
        rows=rows,
        total=total,
        page=page,
        per=per,
        total_pages=total_pages,
        q=q,
        src_filter=source,
        actress=actress,
        sources=sources,
        actresses=actresses,
        local_hints=local_hints,
        item_list_title=item_list_title,
    )


@app.route("/threads")
def threads():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    source = request.args.get("source", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per = 30
    offset = (page - 1) * per

    conds, params = [], []
    if q:
        conds.append("title LIKE ?")
        params.append(f"%{q}%")
    if status:
        conds.append("status = ?")
        params.append(status)
    if source:
        conds.append("source = ?")
        params.append(source)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = db.execute(f"SELECT COUNT(*) FROM threads {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM threads {where} ORDER BY crawled_at DESC LIMIT ? OFFSET ?",
        params + [per, offset],
    ).fetchall()

    sources = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT source FROM threads WHERE source != '' ORDER BY source"
        ).fetchall()
    ]
    total_pages = max(1, (total + per - 1) // per)
    return render_template_string(
        TEMPLATE_THREADS,
        rows=rows,
        total=total,
        page=page,
        per=per,
        total_pages=total_pages,
        q=q,
        status=status,
        src_filter=source,
        sources=sources,
    )


# ── HTML 模板 ────────────────────────────────────────────────
_BASE = """
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CL 数据查看器</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0 }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh }
  a { color: #60a5fa; text-decoration: none }
  a:hover { text-decoration: underline }

  /* ── 顶栏 ── */
  nav { background: #1e2130; border-bottom: 1px solid #2d3248;
        display: flex; align-items: center; gap: 24px; padding: 0 24px; height: 52px }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: #f1f5f9 }
  nav a { color: #94a3b8; font-size: .9rem; padding: 4px 0 }
  nav a.active { color: #60a5fa; border-bottom: 2px solid #60a5fa }

  /* ── 页面主体 ── */
  .page { max-width: 1400px; margin: 0 auto; padding: 28px 24px }
  h1 { font-size: 1.4rem; margin-bottom: 20px; color: #f1f5f9 }

  /* ── 统计卡片 ── */
  .stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px }
  .card { background: #1e2130; border: 1px solid #2d3248; border-radius: 10px;
          padding: 20px 28px; flex: 1; min-width: 160px }
  .card .num { font-size: 2rem; font-weight: 700; color: #60a5fa }
  .card .lbl { font-size: .82rem; color: #64748b; margin-top: 4px }

  /* ── 筛选栏 ── */
  .filters { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px }
  .filters input, .filters select {
    background: #1e2130; border: 1px solid #2d3248; color: #e2e8f0;
    border-radius: 7px; padding: 8px 14px; font-size: .88rem; outline: none }
  .filters input:focus, .filters select:focus { border-color: #60a5fa }
  .filters input[type=text] { flex: 1; min-width: 200px }
  .filters button {
    background: #2563eb; border: none; color: #fff; border-radius: 7px;
    padding: 8px 20px; font-size: .88rem; cursor: pointer }
  .filters button:hover { background: #1d4ed8 }

  /* ── 表格 ── */
  .tbl-wrap { overflow-x: auto }
  table { width: 100%; border-collapse: collapse; font-size: .87rem }
  th { background: #1e2130; color: #94a3b8; font-weight: 500;
       padding: 10px 14px; text-align: left; white-space: nowrap;
       border-bottom: 1px solid #2d3248 }
  td { padding: 10px 14px; border-bottom: 1px solid #1e2535; vertical-align: middle }
  tr:hover td { background: #1a1f35 }

  /* ── 缩略图 ── */
  .thumb {
    width: 256px; height: 176px; object-fit: cover; border-radius: 20px; display: block;
  }
  .no-img { width: 256px; height: 176px; background: #1e2535; border-radius: 20px;
             display: flex; align-items: center; justify-content: center;
             font-size: .7rem; color: #475569 }

  /* ── 标签 ── */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: .75rem }
  .badge-done   { background: #14532d; color: #86efac }
  .badge-pending{ background: #1e3a5f; color: #93c5fd }
  .badge-src    { background: #2d1a4f; color: #c4b5fd }

  /* ── 分页 ── */
  .pager { display: flex; gap: 8px; align-items: center; margin-top: 20px; flex-wrap: wrap }
  .pager a, .pager span {
    padding: 6px 12px; border-radius: 6px; font-size: .85rem;
    background: #1e2130; border: 1px solid #2d3248; color: #94a3b8 }
  .pager a:hover { background: #2d3248; text-decoration: none }
  .pager .cur { background: #2563eb; border-color: #2563eb; color: #fff }
  .pager .info { background: transparent; border: none; color: #64748b }

  /* ── 番号库：全宽三列卡片 ── */
  .page-items { max-width: none; margin: 0; padding: 12px 0 28px; width: 100%; box-sizing: border-box }
  .page-items h1 { padding: 0 0 14px; margin-bottom: 0 }
  .filters-items { padding: 0 0 14px; margin-bottom: 12px; border-bottom: 1px solid #2d3248;
                    flex-wrap: wrap; align-items: center }
  .filters-items input[type=text] { flex: 1; min-width: 160px }
  .items-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    padding: 0;
    width: 100%;
  }
  @media (max-width: 1024px) {
    .items-grid { grid-template-columns: repeat(2, 1fr); }
  }
  @media (max-width: 560px) {
    .items-grid { grid-template-columns: 1fr; }
  }
  .item-card {
    background: #1a1d28;
    border: 1px solid #2d3248;
    border-radius: 12px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .item-thumb-wrap {
    width: 100%;
    aspect-ratio: 256 / 176;
    background: #151821;
  }
  .item-thumb-wrap .thumb {
    width: 100%;
    height: 100%;
    object-fit: cover;
    border-radius: 0;
    display: block;
  }
  .item-thumb-wrap .no-img {
    width: 100%;
    height: 100%;
    border-radius: 0;
    min-height: 0;
  }
  .item-thumb-wrap.thumb-video {
    box-shadow: inset 0 0 0 6px #dc2626;
  }
  .thumb-video-link {
    display: block;
    width: 100%;
    height: 100%;
    text-decoration: none;
    color: inherit;
    outline: none;
    cursor: pointer;
  }
  .thumb-video-link:hover .thumb { opacity: .93 }
  .thumb-video-link .no-img { color: #fde047; font-weight: 600 }
  .item-meta { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 8px }
  .item-row1 {
    display: flex;
    flex-wrap: nowrap;
    align-items: baseline;
    gap: 8px;
    justify-content: flex-start;
    width: 100%;
    font-size: .82rem;
    color: #94a3b8;
  }
  .item-row1-main {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px 12px;
    min-width: 0;
    flex: 0 1 auto;
  }
  .item-row1-tags {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
    align-items: center;
    margin-left: 2px;
  }
  .badge-local {
    font-size: .72rem;
    padding: 2px 8px;
    border-radius: 6px;
    white-space: nowrap;
    font-weight: 500;
  }
  .badge-local-pic { background: #1e3a5f; color: #93c5fd }
  .badge-local-temp {
    background: #292524;
    color: #d6d3d1;
    border: 1px solid #57534e;
  }
  .badge-local-vid { background: #422006; color: #fde047; border: 1px solid #854d0e }
  .badge-local-vid-size {
    font-size: .72rem;
    color: #94a3b8;
    white-space: nowrap;
    margin-left: 2px;
  }
  .item-code {
    font-family: ui-monospace, monospace; color: #a5b4fc; font-weight: 600;
    flex-shrink: 0;
  }
  .item-actress {
    color: #cbd5e1;
    flex: 0 1 auto;
    max-width: min(52%, 16rem);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item-size { color: #64748b; white-space: nowrap; flex-shrink: 0 }
  .item-time { color: #64748b; white-space: nowrap; flex-shrink: 0; font-variant-numeric: tabular-nums }
  .item-title {
    font-size: .88rem;
    color: #60a5fa;
    line-height: 1.38;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  a.item-title { text-decoration: none }
  a.item-title:hover { color: #93c5fd }
  span.item-title-na { color: #64748b !important; cursor: default !important }
  .items-empty { text-align: center; color: #64748b; padding: 48px 10px; margin: 0 }
  .page-items .pager { padding: 0; margin-top: 20px }
</style>
</head>
<body>
<nav>
  <span class="brand">📦 CL 查看器</span>
  <a href="/" class="{{ 'active' if active=='home' else '' }}">首页</a>
  <a href="/items" class="{{ 'active' if active=='items' else '' }}">番号库</a>
  <a href="/threads" class="{{ 'active' if active=='threads' else '' }}">帖子列表</a>
</nav>
{% block body %}{% endblock %}
</body>
</html>
"""

TEMPLATE_INDEX = _BASE.replace(
    "{% block body %}{% endblock %}",
    """
{% set active = 'home' %}
<div class="page">
  <h1>数据概览</h1>
  <div class="stats">
    <div class="card"><div class="num">{{ stats['total_items'] }}</div><div class="lbl">番号条目</div></div>
    <div class="card"><div class="num">{{ stats['total_threads'] }}</div><div class="lbl">帖子总数</div></div>
    <div class="card"><div class="num">{{ stats['done_threads'] }}</div><div class="lbl">已完成帖子</div></div>
    <div class="card"><div class="num">{{ stats['total_actresses'] }}</div><div class="lbl">女优人数</div></div>
    <div class="card"><div class="num">{{ stats['total_sources'] }}</div><div class="lbl">数据来源</div></div>
  </div>
  <div style="display:flex;gap:16px;flex-wrap:wrap">
    <a href="/items" style="display:block;background:#1e2130;border:1px solid #2d3248;border-radius:10px;padding:20px 28px;min-width:200px">
      <div style="font-size:1.5rem;margin-bottom:8px">🎬</div>
      <div style="color:#f1f5f9;font-weight:600">浏览番号库</div>
      <div style="color:#64748b;font-size:.85rem;margin-top:4px">按番号/女优/来源筛选</div>
    </a>
    <a href="/threads" style="display:block;background:#1e2130;border:1px solid #2d3248;border-radius:10px;padding:20px 28px;min-width:200px">
      <div style="font-size:1.5rem;margin-bottom:8px">📋</div>
      <div style="color:#f1f5f9;font-weight:600">浏览帖子列表</div>
      <div style="color:#64748b;font-size:.85rem;margin-top:4px">查看爬取状态与来源</div>
    </a>
  </div>
</div>
""",
)

TEMPLATE_ITEMS = _BASE.replace(
    "{% block body %}{% endblock %}",
    """
{% set active = 'items' %}
<div class="page page-items">
  <h1>番号库 <span style="color:#64748b;font-size:1rem;font-weight:400">共 {{ total }} 条</span></h1>
  <form id="items-filter" class="filters filters-items" method="get">
    <input type="text" name="q" id="items-q" value="{{ q }}" placeholder="筛选番号 / 标题 / 女优（输入后自动生效）" autocomplete="off">
    <select name="source" aria-label="来源" onchange="this.form.submit()">
      <option value="">全部来源</option>
      {% for s in sources %}<option value="{{ s }}" {{ 'selected' if src_filter==s }}>{{ s }}</option>{% endfor %}
    </select>
    <select name="actress" aria-label="女优" onchange="this.form.submit()">
      <option value="">全部女优</option>
      {% for a in actresses %}<option value="{{ a }}" {{ 'selected' if actress==a }}>{{ a }}</option>{% endfor %}
    </select>
    <a href="/items" style="padding:8px 14px;background:#1e2130;border:1px solid #2d3248;border-radius:7px;font-size:.88rem;text-decoration:none;color:#94a3b8">重置</a>
  </form>
  {% if rows %}
  <div class="items-grid">
    {% for r in rows %}
    {% set lh = local_hints.get(r['code']) %}
    {% set vid = lh['video_id'] if lh else None %}
    <article class="item-card">
      <div class="item-thumb-wrap{% if vid %} thumb-video{% endif %}">
        {% if r['img_path'] %}
          {% if vid %}
          <a class="thumb-video-link" href="#" role="button" data-local-play="{{ vid }}" title="本地播放视频（不刷新页面）" aria-label="本地播放视频">
            <img class="thumb" src="/files/item/{{ r['id'] }}/image" alt="" loading="lazy" onerror="this.style.display='none'">
          </a>
          {% else %}
          <img class="thumb" src="/files/item/{{ r['id'] }}/image" alt="" loading="lazy" onerror="this.style.display='none'">
          {% endif %}
        {% elif r['img_url'] %}
          {% if vid %}
          <a class="thumb-video-link" href="#" role="button" data-local-play="{{ vid }}" title="本地播放视频（不刷新页面）" aria-label="本地播放视频">
            <img class="thumb" src="{{ r['img_url'] }}" alt="" loading="lazy" onerror="this.style.display='none'">
          </a>
          {% else %}
          <img class="thumb" src="{{ r['img_url'] }}" alt="" loading="lazy" onerror="this.style.display='none'">
          {% endif %}
        {% else %}
          {% if vid %}
          <a class="thumb-video-link" href="#" role="button" data-local-play="{{ vid }}" title="本地播放视频（不刷新页面）" aria-label="本地播放视频"><div class="no-img">视频</div></a>
          {% else %}
          <div class="no-img">无图</div>
          {% endif %}
        {% endif %}
      </div>
      <div class="item-meta">
        <div class="item-row1">
          <div class="item-row1-main">
            <span class="item-code">{{ r['code'] }}</span>
            <span class="item-actress" title="{{ r['actress'] or '' }}">{{ r['actress'] or '—' }}</span>
            <span class="item-size">{{ r['size_gb'] or '—' }}GB</span>
            <span class="item-time">{{ r['crawled_at'][:16] if r['crawled_at'] else '—' }}</span>
          </div>
          <div class="item-row1-tags">
            {% if lh and lh['picpic'] %}<span class="badge-local badge-local-pic">图片</span>{% endif %}
            {% if lh and vid %}<span class="badge-local badge-local-vid">视频</span>{% if lh['video_size_gb'] %}<span class="badge-local-vid-size">{{ lh['video_size_gb'] }} GB</span>{% endif %}{% endif %}
            {% if lh and lh['video_temp'] %}<span class="badge-local badge-local-temp">temp</span>{% endif %}
          </div>
        </div>
        {% if r['torrent_path'] %}
        <a class="item-title" href="/files/item/{{ r['id'] }}/magnet">{{ item_list_title[r['id']] }}</a>
        {% elif r['torrent_url'] %}
        <a class="item-title" href="{{ r['torrent_url'] }}">{{ item_list_title[r['id']] }}</a>
        {% else %}
        <span class="item-title item-title-na">{{ item_list_title[r['id']] }}</span>
        {% endif %}
      </div>
    </article>
    {% endfor %}
  </div>
  {% else %}
  <p class="items-empty">暂无数据</p>
  {% endif %}
  {% if total_pages > 1 %}
  <div class="pager">
    <span class="info">第 {{ page }}/{{ total_pages }} 页</span>
    {% if page > 1 %}<a href="?q={{ q }}&source={{ src_filter }}&actress={{ actress }}&page={{ page-1 }}">上一页</a>{% endif %}
    {% for p in range([1, page-2]|max, [total_pages+1, page+3]|min) %}
      {% if p == page %}<span class="cur">{{ p }}</span>
      {% else %}<a href="?q={{ q }}&source={{ src_filter }}&actress={{ actress }}&page={{ p }}">{{ p }}</a>{% endif %}
    {% endfor %}
    {% if page < total_pages %}<a href="?q={{ q }}&source={{ src_filter }}&actress={{ actress }}&page={{ page+1 }}">下一页</a>{% endif %}
  </div>
  {% endif %}
</div>
<script>
(function () {
  var form = document.getElementById("items-filter");
  if (!form) return;
  var inp = document.getElementById("items-q");
  var tm;
  if (inp) {
    inp.addEventListener("input", function () {
      clearTimeout(tm);
      tm = setTimeout(function () { form.submit(); }, 420);
    });
    inp.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        clearTimeout(tm);
        form.submit();
      }
    });
  }
})();
(function () {
  var grid = document.querySelector(".items-grid");
  if (!grid) return;
  grid.addEventListener("click", function (ev) {
    var a = ev.target.closest("a.thumb-video-link[data-local-play]");
    if (!a) return;
    ev.preventDefault();
    var id = a.getAttribute("data-local-play");
    if (!id) return;
    fetch("/local/play/" + encodeURIComponent(id), {
      method: "GET",
      credentials: "same-origin",
    }).catch(function () {});
  });
})();
</script>
""",
)

TEMPLATE_THREADS = _BASE.replace(
    "{% block body %}{% endblock %}",
    """
{% set active = 'threads' %}
<div class="page">
  <h1>帖子列表 <span style="color:#64748b;font-size:1rem;font-weight:400">共 {{ total }} 条</span></h1>
  <form class="filters" method="get">
    <input type="text" name="q" value="{{ q }}" placeholder="搜索标题…">
    <select name="status">
      <option value="">全部状态</option>
      <option value="done"    {{ 'selected' if status=='done' }}>done</option>
      <option value="pending" {{ 'selected' if status=='pending' }}>pending</option>
    </select>
    <select name="source">
      <option value="">全部来源</option>
      {% for s in sources %}<option value="{{ s }}" {{ 'selected' if src_filter==s }}>{{ s }}</option>{% endfor %}
    </select>
    <button type="submit">搜索</button>
    <a href="/threads" style="padding:8px 14px;background:#1e2130;border:1px solid #2d3248;border-radius:7px;font-size:.88rem">重置</a>
  </form>
  <div class="tbl-wrap">
  <table>
    <thead><tr>
      <th>标题</th><th>来源</th><th>状态</th><th>下载量</th><th>详情页</th><th>入库时间</th>
    </tr></thead>
    <tbody>
    {% for r in rows %}
    <tr>
      <td style="max-width:420px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{{ r['title'] or '' }}">
        {{ r['title'] or '（无标题）' }}
      </td>
      <td><span class="badge badge-src">{{ r['source'] or '—' }}</span></td>
      <td>
        {% if r['status'] == 'done' %}<span class="badge badge-done">done</span>
        {% else %}<span class="badge badge-pending">{{ r['status'] }}</span>{% endif %}
      </td>
      <td>{{ r['downloads'] or 0 }}</td>
      <td><a href="{{ r['url'] }}" target="_blank">🔗</a></td>
      <td style="color:#64748b;font-size:.8rem;white-space:nowrap">{{ r['crawled_at'][:16] if r['crawled_at'] else '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6" style="text-align:center;color:#64748b;padding:40px">暂无数据</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% if total_pages > 1 %}
  <div class="pager">
    <span class="info">第 {{ page }}/{{ total_pages }} 页</span>
    {% if page > 1 %}<a href="?q={{ q }}&status={{ status }}&source={{ src_filter }}&page={{ page-1 }}">上一页</a>{% endif %}
    {% for p in range([1, page-2]|max, [total_pages+1, page+3]|min) %}
      {% if p == page %}<span class="cur">{{ p }}</span>
      {% else %}<a href="?q={{ q }}&status={{ status }}&source={{ src_filter }}&page={{ p }}">{{ p }}</a>{% endif %}
    {% endfor %}
    {% if page < total_pages %}<a href="?q={{ q }}&status={{ status }}&source={{ src_filter }}&page={{ page+1 }}">下一页</a>{% endif %}
  </div>
  {% endif %}
</div>
""",
)


# ── 入口 ────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="SQLite 数据 Web 查看器")
    p.add_argument("--db", default="D:/data/cl_db/cl.db", help="SQLite 数据库路径")
    p.add_argument("--host", default="127.0.0.1", help="监听地址")
    p.add_argument("--port", default=5000, type=int, help="端口（默认 5000）")
    p.add_argument(
        "--no-reload",
        action="store_true",
        help="关闭监视源码变更并自动重启（默认开启，保存 web_viewer.py 等后会重启）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    DB_PATH = Path(args.db)
    if not DB_PATH.exists():
        print(f"[警告] 数据库文件不存在: {DB_PATH}")
    else:
        print(f"[OK] 数据库: {DB_PATH}")
    print(f"[OK] 浏览器打开 → http://{args.host}:{args.port}")
    use_reloader = not args.no_reload
    if use_reloader:
        print(
            "[提示] 已开启代码热重载：修改本项目 .py 保存后会自动重启（加 --no-reload 可关闭）"
        )
    app.run(host=args.host, port=args.port, debug=False, use_reloader=use_reloader)
