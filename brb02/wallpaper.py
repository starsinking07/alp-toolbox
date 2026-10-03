# -*- coding: utf-8 -*-
"""Wallpaper Engine 联动 (实验性): 尽力定位当前壁纸的预览图。

原理: 通过 Steam 注册表定位 Wallpaper Engine 安装目录, 解析其 config.json
与工程目录, 返回一张可用图片路径。WE 动态壁纸工程自带 preview.jpg。
找不到任何东西时返回 None (调用方降级为无壁纸)。
"""
from __future__ import annotations

import json
import os
import re

W_E = '431960'   # Wallpaper Engine 的 Steam AppID


def _steam_path() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            return winreg.QueryValueEx(k, 'SteamPath')[0]
    except OSError:
        return None


def _libraries(steam: str) -> list[str]:
    libs = [steam]
    vdf = os.path.join(steam, 'steamapps', 'libraryfolders.vdf')
    try:
        txt = open(vdf, encoding='utf-8', errors='ignore').read()
        libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', txt)]
    except OSError:
        pass
    return libs


def find_we_dir() -> str | None:
    steam = _steam_path()
    if not steam:
        return None
    for lib in _libraries(steam):
        cand = os.path.join(lib, 'steamapps', 'common', 'wallpaper_engine')
        if os.path.isdir(cand):
            return cand
    return None


def _project_preview(proj_dir: str) -> str | None:
    """工程目录 → 预览图 (优先 preview.jpg; 视频壁纸用它做静态背景)"""
    for name in ('preview.jpg', 'preview.png'):
        p = os.path.join(proj_dir, name)
        if os.path.isfile(p):
            return p
    return None


def _walk_for_paths(o, found: list):
    if isinstance(o, dict):
        for v in o.values():
            _walk_for_paths(v, found)
    elif isinstance(o, list):
        for v in o:
            _walk_for_paths(v, found)
    elif isinstance(o, str):
        if re.search(r'(content[/\\]+' + W_E + r'|myprojects)', o) and \
                re.search(r'\.(jpg|jpeg|png|mp4|webm|webp)$', o, re.I):
            found.append(o)


def find_we_wallpaper() -> str | None:
    """返回当前 Wallpaper Engine 壁纸的可用图片路径 (尽力而为)。"""
    we = find_we_dir()
    if not we:
        return None

    # 1) config.json 里递归找壁纸引用
    try:
        data = json.load(open(os.path.join(we, 'config.json'),
                              encoding='utf-8', errors='ignore'))
        found: list[str] = []
        _walk_for_paths(data, found)
        for f in found:
            proj = os.path.dirname(f.replace('/', os.sep))
            pj = _project_preview(proj)
            if pj:
                return pj
    except Exception:
        pass

    # 2) 回退: 最近修改的工程 (workshop / myprojects)
    cands: list[tuple[float, str]] = []
    for rel in (os.path.join('steamapps', 'workshop', 'content', W_E),
                os.path.join('projects', 'myprojects')):
        rd = os.path.join(we, rel)
        if not os.path.isdir(rd):
            continue
        for name in os.listdir(rd):
            pj = _project_preview(os.path.join(rd, name))
            if pj:
                cands.append((os.path.getmtime(pj), pj))
    if cands:
        cands.sort(reverse=True)
        return cands[0][1]
    return None
