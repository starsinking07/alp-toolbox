# -*- coding: utf-8 -*-
"""GitHub Releases 更新检查 (v0.1.5)。

纯函数部分 (parse_version / is_newer / pick_release / pick_setup_asset / summarize /
parse_tag_from_url / setup_download_url) 可离线单测; UpdateChecker / AssetDownloader
基于 QtNetwork, 由关于页在 UI 线程异步使用, 不阻塞界面。只读公开信息, 不上传本机数据。

通道策略: 正式版走 releases/latest 的 302 跳转端点 (无 API 配额, 共享出口 IP 也稳);
预发布走 api.github.com (未登录限 60 次/小时/IP)。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest,
)

REPO_OWNER = 'starsinking07'
REPO_NAME = 'alp-toolbox'
REPO_URL = f'https://github.com/{REPO_OWNER}/{REPO_NAME}'
RELEASES_URL = REPO_URL + '/releases'
LATEST_URL = RELEASES_URL + '/latest'
API_RELEASES_URL = f'https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=10'
EXPANDED_ASSETS_URL = RELEASES_URL + '/expanded_assets/'   # + tag: 附件真实直链碎片页
SETUP_PREFIX = 'Alp工具箱-setup'          # 安装包附件命名前缀 (spec OutFile)
USER_AGENT = 'Alp-Toolbox-Updater'


def parse_version(tag: str) -> tuple[int, ...]:
    """'v0.1.10-beta' → (0, 1, 10)。忽略非数字尾巴; 解析不出数字 → (0,)。"""
    t = (tag or '').strip().lstrip('vV')
    head = t.split('-', 1)[0].split('+', 1)[0]
    parts: list[int] = []
    for piece in head.split('.'):
        num = ''.join(ch for ch in piece if ch.isdigit())
        if not num:
            break
        parts.append(int(num))
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    """逐段比较, 'v0.1.10' > 'v0.1.9' → True。"""
    return parse_version(latest) > parse_version(current)


def setup_asset_name(version: str) -> str:
    """'0.1.5' → 'Alp工具箱-setup-v0.1.5.exe' (与 Release 附件命名一致)。"""
    return f'{SETUP_PREFIX}-v{version}.exe'


def setup_download_url(version: str) -> str:
    """构造 setup 附件直链 (browser 端 302 到签名对象存储, 不占 API 配额)。"""
    return f'{REPO_URL}/releases/download/v{version}/{setup_asset_name(version)}'


def parse_tag_from_url(url: str) -> Optional[str]:
    """'.../releases/tag/v0.1.6' → 'v0.1.6' (原样保留, 含 v 前缀); 非 tag 地址 → None。"""
    m = re.search(r'/releases/tag/([^/?#]+)', url or '')
    if not m:
        return None
    tag = urllib.parse.unquote(m.group(1))
    return tag or None


def setup_url_from_assets_html(html: str) -> Optional[str]:
    """从 releases/expanded_assets 碎片页解析 setup 安装包真实直链。

    GitHub 会把中文附件名改写 (工具箱 → .), 按命名规则拼 URL 会 404 (v0.1.6 实测);
    碎片页里的 href 是真实直链, 彻底绕开命名问题。优先含 'setup' 的 exe。"""
    hrefs = re.findall(r'href="(/[^"]*?/releases/download/[^"]+?\.exe)"', html or '')
    if not hrefs:
        return None
    hrefs = [h.replace('&amp;', '&') for h in hrefs]
    setup = [h for h in hrefs if 'setup' in h.lower()]
    return 'https://github.com' + (setup or hrefs)[0]


def pick_release(releases: list[dict], channel: str = 'stable') -> Optional[dict]:
    """从 /releases 列表 (新→旧) 选目标版本。

    channel: 'stable' 只认正式版; 'pre' 认最新非草稿 (含预发布)。
    """
    for rel in releases or []:
        if rel.get('draft'):
            continue
        if channel == 'stable' and rel.get('prerelease'):
            continue
        return rel
    return None


def pick_setup_asset(release: dict) -> Optional[dict]:
    """挑 setup 安装包附件 (名字含 setup 的 exe; 中文文件名可能被 GitHub 改写)。"""
    for asset in release.get('assets') or []:
        name = asset.get('name') or ''
        if 'setup' in name.lower() and name.lower().endswith('.exe'):
            return {'name': name,
                    'url': asset.get('browser_download_url') or '',
                    'size': asset.get('size') or 0}
    return None


def summarize(release: dict) -> Optional[dict]:
    """API release 对象 → 页面用的摘要 dict; 无效输入 → None。"""
    if not release or not release.get('tag_name'):
        return None
    return {
        'tag': release['tag_name'],
        'version': release['tag_name'].lstrip('vV'),
        'prerelease': bool(release.get('prerelease')),
        'notes': release.get('body') or '',
        'html_url': release.get('html_url') or RELEASES_URL,
        'setup': pick_setup_asset(release),
    }


class UpdateChecker(QObject):
    """异步检查最新版本。

    正式版: GET releases/latest → 302 Location 含 /releases/tag/vX → 解析版本;
            不占用 api.github.com 配额 (未登录 60 次/小时/IP)。
    预发布: api.github.com /releases 列表 (可能被限流, 报错会注明)。
    done(摘要dict|None) — None 表示没有可用发布; fail(原因) — 网络/HTTP/解析失败。
    """

    done = Signal(object)
    fail = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._nam.setTransferTimeout(15000)
        self._reply: Optional[QNetworkReply] = None
        self._seq = 0                        # 请求代次: 发新请求后旧回调一律作废

    def check(self, channel: str = 'stable'):
        self._seq += 1                       # 先升代次再 abort, 旧请求的取消回调不会误发 fail
        seq = self._seq
        self.abort()
        req = QNetworkRequest(QUrl(LATEST_URL if channel == 'stable' else API_RELEASES_URL))
        if channel != 'stable':
            req.setRawHeader(b'Accept', b'application/vnd.github+json')
        req.setRawHeader(b'User-Agent', USER_AGENT.encode('ascii'))
        # stable 端点要读 302 的 Location → 手动重定向; pre 列表允许跟随
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.ManualRedirectPolicy if channel == 'stable'
                         else QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self._nam.get(req)
        self._reply = reply
        finisher = self._on_html_finished if channel == 'stable' else self._on_api_finished
        reply.finished.connect(lambda r=reply, s=seq: finisher(r, s))
        reply.errorOccurred.connect(lambda code, r=reply, s=seq: self._on_error(r, code, s))

    def abort(self):
        if self._reply is not None:
            self._reply.abort()
            self._reply.deleteLater()
            self._reply = None

    def _claim(self, reply: QNetworkReply) -> bool:
        """只有未被新请求取代的 reply 才继续处理。"""
        if self._reply is not reply:
            reply.deleteLater()
            return False
        self._reply = None
        return True

    def _on_html_finished(self, reply: QNetworkReply, seq: int):
        if seq != self._seq:                 # 旧代次回调: 请求已被更新, 只回收对象
            reply.deleteLater()
            return
        if not self._claim(reply):
            return
        try:
            self._handle_html(reply)
        except Exception as e:               # Qt 槽里的异常会被事件循环吞掉 → 必须显式报错
            import traceback
            traceback.print_exc()
            self.fail.emit(f'检查异常: {e!r}')

    def _handle_html(self, reply: QNetworkReply):
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status == 404:
            reply.deleteLater()
            self.done.emit(None)               # 仓库还没有发布
            return
        if status in (301, 302, 303, 307, 308):
            location = bytes(reply.rawHeader('Location')).decode('utf-8', 'replace')
            tag_raw = parse_tag_from_url(location)
            reply.deleteLater()
            if not tag_raw:
                self.fail.emit(f'跳转地址无法解析版本: {location[:120]}')
                return
            self._fetch_assets(tag_raw)   # 解析真实附件直链 (中文文件名被 GitHub 改写)
            return
        reply.deleteLater()
        if status is None:
            return
        self.fail.emit(f'HTTP {status} ({LATEST_URL})')

    def _fetch_assets(self, tag_raw: str):
        """拉取 expanded_assets 碎片页, 解析 Release 真实附件直链。

        GitHub 会把中文附件名改写 (工具箱 → .), 按命名规则拼 URL 会 404 (v0.1.6 实测);
        碎片页里的 href 是真实直链, 彻底绕开命名问题。"""
        req = QNetworkRequest(QUrl(EXPANDED_ASSETS_URL + tag_raw))
        req.setRawHeader(b'User-Agent', USER_AGENT.encode('ascii'))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self._nam.get(req)
        self._reply = reply
        seq = self._seq
        reply.finished.connect(lambda r=reply, s=seq: self._on_assets_finished(r, tag_raw, s))
        reply.errorOccurred.connect(lambda code, r=reply, s=seq: self._on_error(r, code, s))

    def _on_assets_finished(self, reply: QNetworkReply, tag_raw: str, seq: int):
        if seq != self._seq:                 # 旧代次回调: 请求已被更新, 只回收对象
            reply.deleteLater()
            return
        if not self._claim(reply):
            return
        try:
            self._handle_assets(reply, tag_raw)
        except Exception as e:               # 槽内异常显式上报 (项目铁律)
            import traceback
            traceback.print_exc()
            self.fail.emit(f'检查异常: {e!r}')

    def _handle_assets(self, reply: QNetworkReply, tag_raw: str):
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        html = bytes(reply.readAll()).decode('utf-8', 'replace')
        reply.deleteLater()
        ver = tag_raw.lstrip('vV')
        url = setup_url_from_assets_html(html) if status == 200 else None
        if not url:
            # 碎片不可得 (失败/无附件): 回退按命名规则拼直链 (ASCII 附件名时可用)
            url = setup_download_url(ver)
        self.done.emit(self._summary(ver, tag_raw, url))

    @staticmethod
    def _summary(ver: str, tag_raw: str, setup_url: str) -> dict:
        name = urllib.parse.unquote(setup_url.rsplit('/', 1)[-1])
        return {'tag': tag_raw if tag_raw[:1] in ('v', 'V') else 'v' + tag_raw,
                'version': ver, 'prerelease': False, 'notes': '',
                'html_url': RELEASES_URL + '/tag/' + tag_raw,
                'setup': {'name': name, 'url': setup_url, 'size': 0}}

    def _on_api_finished(self, reply: QNetworkReply, seq: int):
        if seq != self._seq:                 # 旧代次回调: 请求已被更新, 只回收对象
            reply.deleteLater()
            return
        if not self._claim(reply):
            return
        try:
            self._handle_api(reply)
        except Exception as e:               # 同上: 槽内异常显式上报
            import traceback
            traceback.print_exc()
            self.fail.emit(f'检查异常: {e!r}')

    def _handle_api(self, reply: QNetworkReply):
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        data = bytes(reply.readAll())
        reply.deleteLater()
        if status is None:
            return
        if status != 200:
            msg = ''
            try:
                msg = str(json.loads(data.decode('utf-8')).get('message') or '')
            except Exception:
                pass
            remaining = bytes(reply.rawHeader('X-RateLimit-Remaining')).decode('ascii', 'replace')
            if status == 403 and (remaining == '0' or 'rate limit' in msg.lower()):
                reset = bytes(reply.rawHeader('X-RateLimit-Reset')).decode('ascii', 'replace')
                wait = ''
                if reset.isdigit():
                    wait = f' (~{max(1, (int(reset) - int(time.time())) // 60 + 1)} 分钟后重置)'
                self.fail.emit(f'GitHub API 限流 (未登录每 IP 每小时 60 次){wait}; '
                               f'可切回「正式版」通道 (无配额)')
            else:
                self.fail.emit(f'HTTP {status}: {msg[:120]}' if msg else f'HTTP {status}')
            return
        try:
            releases = json.loads(data.decode('utf-8'))
        except Exception as e:
            self.fail.emit(f'应答解析失败 {e!r}')
            return
        self.done.emit(summarize(pick_release(releases, 'pre')))

    def _on_error(self, reply: QNetworkReply, code: int, seq: int):
        if seq != self._seq:                 # 旧代次 (含 abort 引发的取消错误): 不上报
            reply.deleteLater()
            return
        if self._reply is not reply:     # 已被更新的请求取代
            return
        self._reply = None
        self.fail.emit(reply.errorString() or f'网络错误 {code}')


class AssetDownloader(QObject):
    """下载 release 附件到本地文件。

    progress(已收字节, 总字节) / done(本地路径) / fail(原因)。
    自我保护: 写盘失败 / 下载停滞 / 下载内容为空或大小不符都会终止并删除残件,
    防止磁盘满等场景产出的截断安装包被直接执行。
    """

    progress = Signal(int, int)
    done = Signal(str)
    fail = Signal(str)

    STALL_CHECK_MS = 15000     # 看门狗巡检周期
    STALL_TIMEOUT_SECS = 90    # 超过该时长无任何进度视为停滞

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)   # 默认无传输超时, 适合大文件
        self._reply: Optional[QNetworkReply] = None
        self._file = None
        self._dest = ''
        self._expect_size = 0                # 期望大小, 来自 asset['size']; 0 表示未知
        self._seq = 0                        # 请求代次: 重新下载后旧回调一律作废
        self._failed = False                 # 本代次是否已发过 fail (防重复上报)
        self._last_progress_ts = 0.0
        self._watchdog = QTimer(self)        # 下载停滞看门狗
        self._watchdog.setInterval(self.STALL_CHECK_MS)
        self._watchdog.timeout.connect(self._check_stall)

    def download(self, url: str, dest: str, expect_size: int = 0):
        """开始下载; expect_size 传 asset['size'], >0 时完成后校验文件大小。"""
        self._seq += 1                       # 先升代次再 abort, 旧请求回调全部作废
        seq = self._seq
        self.abort()
        self._dest = dest
        self._expect_size = max(0, int(expect_size or 0))
        self._failed = False
        self._last_progress_ts = time.time()
        try:
            self._file = open(dest, 'wb')
        except OSError as e:                 # 磁盘满/路径无效等 → 显式报错, 不在槽内静默吞掉
            self.fail.emit(f'无法创建本地文件: {e}')
            return
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b'User-Agent', USER_AGENT.encode('ascii'))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        self._reply = self._nam.get(req)
        self._reply.downloadProgress.connect(
            lambda received, total, s=seq: self._on_progress(received, total, s))
        self._reply.readyRead.connect(lambda s=seq: self._on_ready(s))
        self._reply.finished.connect(lambda s=seq: self._on_finished(s))
        self._watchdog.start()

    def abort(self):
        self._watchdog.stop()
        if self._reply is not None:
            self._reply.abort()
            self._reply.deleteLater()
            self._reply = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def _check_stall(self):
        """看门狗: 长时间无任何进度 → 判定停滞, 终止并报错。"""
        if self._reply is None:
            self._watchdog.stop()
            return
        if time.time() - self._last_progress_ts > self.STALL_TIMEOUT_SECS:
            self._fail_download('下载停滞, 请重试')

    def _fail_download(self, msg: str):
        """终止当前下载并删除残件, 只发一次 fail。"""
        self._failed = True
        self._watchdog.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()                    # 触发的 finished 在 _on_finished 的 _failed 分支只做清理
            reply.deleteLater()
        f, self._file = self._file, None
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        self._remove_partial()
        self.fail.emit(msg)

    def _remove_partial(self):
        if self._dest:
            try:
                os.remove(self._dest)
            except OSError:
                pass

    def _on_progress(self, received: int, total: int, seq: int):
        if seq != self._seq:                 # 旧代次回调: 忽略
            return
        self._last_progress_ts = time.time()
        self.progress.emit(int(received), int(total))

    def _on_ready(self, seq: int):
        if seq != self._seq:                 # 旧代次回调: 忽略
            return
        if self._file is None or self._reply is None:
            return
        try:
            self._file.write(bytes(self._reply.readAll()))
        except OSError as e:                 # 写盘失败 (磁盘满等) → 终止+删残件, 防截断包被直接执行
            self._fail_download(f'写入本地文件失败: {e}')
            return
        self._last_progress_ts = time.time()

    def _on_finished(self, seq: int):
        if seq != self._seq:                 # 旧代次回调 (被 abort 的请求): 忽略
            return
        self._watchdog.stop()                # done/fail 前先停表
        reply, self._reply = self._reply, None
        f, self._file = self._file, None
        err = None
        if reply is not None:
            err = reply.error()
            reply.deleteLater()
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        if self._failed:                     # 写盘失败/看门狗路径已发过 fail, 这里只做清理
            return
        if err is not None and err != QNetworkReply.NoError:
            self._remove_partial()
            self.fail.emit(reply.errorString() if reply is not None else '下载失败')
            return
        # 完整性校验: 防止磁盘满等产出截断安装包被直接执行
        try:
            actual = os.path.getsize(self._dest)
        except OSError:
            actual = 0
        if self._expect_size > 0 and actual != self._expect_size:
            self._remove_partial()
            self.fail.emit(f'安装包大小不符 (期望 {self._expect_size} 字节, 实际 {actual} 字节)')
            return
        if self._expect_size == 0 and actual <= 0:
            self._remove_partial()
            self.fail.emit('安装包大小不符 (下载内容为空)')
            return
        self.done.emit(self._dest)
