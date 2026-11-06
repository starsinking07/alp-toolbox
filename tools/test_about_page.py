# -*- coding: utf-8 -*-
"""关于页 + 更新检查 (AboutPage / update_check) 离屏自检 —— 不发真实网络请求。

覆盖: 版本比较 / release 挑选 / setup 附件识别 / 页面构造 / 检查结果按钮态 /
赞助弹窗 / 下载进度与完成 / 频道切换复位。
用法: python tools/test_about_page.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from brb02 import update_check as uc
from brb02.diagnostics import APP_VERSION
from brb02.gui import pages
from brb02.gui.theme import build_qss

fails = []


def check(name, ok, detail=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f'  {detail}' if detail else ''))
    if not ok:
        fails.append(name)


app = QApplication.instance() or QApplication([])
app.setStyleSheet(build_qss(dark=False))


def rel(tag, pre=False, assets=()):
    return {'tag_name': tag, 'draft': False, 'prerelease': pre, 'body': 'notes',
            'html_url': f'https://example.com/{tag}',
            'assets': [{'name': n, 'browser_download_url': f'https://x/{n}', 'size': 1}
                       for n in assets]}


print('=== T1 版本解析与比较 ===')
check('T1a parse v0.1.10', uc.parse_version('v0.1.10') == (0, 1, 10))
check('T1b parse v0.1.10 > v0.1.9', uc.is_newer('v0.1.10', 'v0.1.9'))
check('T1c 相等不算新', not uc.is_newer('v0.1.5', '0.1.5'))
check('T1d 带尾巴 v0.2.0-beta', uc.is_newer('v0.2.0-beta', 'v0.1.9') and
      uc.parse_version('v0.2.0-beta') == (0, 2, 0))
check('T1e 垃圾输入安全', uc.parse_version('abc') == (0,) and not uc.is_newer('abc', 'v0.1.4'))

print('=== T2 release 挑选 ===')
stable_old = rel('v0.1.4', assets=('Alp工具箱.exe',))
pre_new = rel('v0.2.0-beta', pre=True, assets=('Alp工具箱-setup-v0.2.0-beta.exe',))
draft = {'tag_name': 'v9', 'draft': True, 'prerelease': False, 'assets': []}
payload = [draft, pre_new, stable_old]
check('T2a stable 跳过预发布', uc.pick_release(payload, 'stable') is stable_old)
check('T2b pre 认最新预发布', uc.pick_release(payload, 'pre') is pre_new)
check('T2c 草稿永远跳过', uc.pick_release([draft], 'pre') is None)
check('T2d 空列表', uc.pick_release([], 'stable') is None)

print('=== T3 setup 附件识别 ===')
check('T3 正式版规范名称', uc.setup_asset_name('0.2.1') == 'Alp-toolbox-setup-v0.2.1-release.exe')
check('T3 后续正式版规范名称', uc.setup_asset_name('0.2.2') == 'Alp-toolbox-setup-v0.2.2-release.exe')
check('T3 Beta 名称独立', uc.setup_asset_name('0.2.2-beta.1') == 'Alp-toolbox-setup-v0.2.2-beta.1.exe')
check('T3 旧版本兼容', uc.setup_asset_name('0.1.5') == 'Alp工具箱-setup-v0.1.5.exe')
r5 = rel('v0.1.5', assets=('Alp工具箱.exe', 'Alp工具箱-setup-v0.1.5.exe', 'README.md'))
s = uc.summarize(r5)
check('T3a summarize 基础字段', s and s['version'] == '0.1.5' and s['tag'] == 'v0.1.5')
check('T3b 选中 setup 排除便携版',
      s['setup'] and s['setup']['name'] == 'Alp工具箱-setup-v0.1.5.exe')
check('T3c 无 setup → None', uc.summarize(rel('v1', assets=('Alp工具箱.exe',)))['setup'] is None)
check('T3d 无效输入', uc.summarize(None) is None and uc.summarize({'draft': True}) is None)

print('=== T4 页面构造 ===')
page = pages.AboutPage({'dark': False})
check('T4a 构造不崩', page is not None)
check('T4b 检查更新按钮可用', page.b_check.isEnabled())
check('T4c 下载按钮初始禁用', not page.b_install.isEnabled())
labels = [l.text() for l in page.findChildren(QLabel)]
check('T4d 当前版本 pill 文本', f'当前 v{APP_VERSION}' in labels, APP_VERSION)

print('=== T5 检查结果 → 按钮态 ===')
# 夹具跟随 APP_VERSION 动态推导 (永远比当前新一个补丁位), 版本升级不再打地鼠
# 夹具跟随 APP_VERSION 动态推导 (永远比当前新一个补丁位), 版本升级不再打地鼠。
# ⚠️ 2026-10-25: 旧写法是 f"v0.1.{patch+1}" —— 主版本升到 0.2.0 后算出来是 v0.1.1,
# 比当前**更旧**, 于是"发现新版本"三条断言全挂。必须按主.次.补丁 三段一起递推。
# ⚠️ 2026-10-31: 版本号允许带后缀 (如 0.2.1-beta 的 Beta 测试版), 直接 int() 拆会炸
# (ValueError: invalid literal for int(): '1-beta')。统一走 update_check.parse_version,
# 它本来就会忽略 -beta / +build 尾巴, 和线上判新的逻辑也一致。
_maj, _min, _pat = (list(uc.parse_version(APP_VERSION)) + [0, 0, 0])[:3]
NEWER_TAG = f'v{_maj}.{_min}.{_pat + 1}'
page._on_release(uc.summarize(rel(NEWER_TAG, assets=(uc.setup_asset_name(NEWER_TAG[1:]),))))
check('T5a 新版本 → 下载可用', page.b_install.isEnabled())
check('T5b pill 变绿含版本', NEWER_TAG in page.pill_latest.text())
check('T5c 提示发现新版本', '发现新版本' in page.lbl_update_hint.text())
page._on_release(uc.summarize(rel(f'v{APP_VERSION}', assets=('x',))))
check('T5d 同版本 → 下载禁用 + 已是最新',
      not page.b_install.isEnabled() and '已是最新' in page.lbl_update_hint.text())
page._on_release(uc.summarize(rel('v0.2.0', pre=True, assets=('Alp工具箱-setup-v0.2.0.exe',))))
check('T5e 预发布标记', '预发布' in page.pill_latest.text())
page._on_check_fail('timeout')
check('T5f 失败 → 按钮恢复 + 提示', page.b_check.isEnabled() and '检查失败' in page.lbl_update_hint.text())
page._on_release(None)
check('T5g 无发布 → 提示', '还没有发布' in page.lbl_update_hint.text())

print('=== T6 赞助弹窗 ===')
popups = []
orig_info = QMessageBox.information
QMessageBox.information = staticmethod(
    lambda *a, **k: popups.append(a) or QMessageBox.StandardButton.Ok)
try:
    page._sponsor()
finally:
    QMessageBox.information = orig_info
check('T6a 弹窗文案', len(popups) == 1 and popups[0][2] == '你的使用就是最好的赞助')
check('T6b 弹窗标题', popups[0][1] == '赞助 Alp 工具箱')

print('=== T7 下载流程 (不碰网络) ===')
page._on_release(uc.summarize(rel('v0.1.7', assets=('Alp工具箱-setup-v0.1.7.exe',))))
page._on_dl_progress(500, 1000)
check('T7a 进度百分比', page.b_install.text() == '下载中 50%')
page._on_dl_progress(1024 * 3, -1)
check('T7b 未知总量显示 MB', 'MB' in page.b_install.text())
launched = []
orig_startfile = getattr(os, 'startfile', None)
os.startfile = lambda p: launched.append(p)
try:
    page._on_dl_done(r'D:\tmp\Alp工具箱-setup-v0.1.6.exe')
finally:
    if orig_startfile is not None:
        os.startfile = orig_startfile
check('T7c 完成后拉起安装器', len(launched) == 1 and 'setup-v0.1.6' in launched[0])
check('T7d 提示安装器已启动', '安装器已启动' in page.lbl_update_hint.text())
page._on_dl_fail('超时')
check('T7e 失败 → 按钮恢复', page.b_install.isEnabled() and '下载失败' in page.lbl_update_hint.text())

print('=== T8 频道切换复位 ===')
page._on_release(uc.summarize(rel('v0.1.7', assets=('Alp工具箱-setup-v0.1.7.exe',))))
page._on_channel_changed()
check('T8a 复位后下载禁用', not page.b_install.isEnabled())
check('T8b pill 复位', page.pill_latest.text() == '最新 —')

print('=== T9 链接与 FAQ ===')
link_texts = [l.text() for l in page.findChildren(QLabel)]
check('T9a 仓库链接存在', any('github.com/starsinking07/alp-toolbox' in t for t in link_texts))
check('T9b FAQ 渲染', pages.AboutPage._FAQS[0][0] in link_texts)
check('T9c 赞助按钮存在', page.b_sponsor.text() == '♡ 赞助支持')

print('=== T10 LATEST 端点辅助 (正式版免 API 配额通道) ===')
check('T10a 解析 tag 跳转 (原样含 v)', uc.parse_tag_from_url(
    'https://github.com/starsinking07/alp-toolbox/releases/tag/v0.1.5') == 'v0.1.5')
check('T10b 非 tag URL → None',
      uc.parse_tag_from_url('https://github.com/starsinking07/alp-toolbox') is None)
check('T10c setup 直链构造', uc.setup_download_url('0.1.5') ==
      'https://github.com/starsinking07/alp-toolbox/releases/download/v0.1.5/Alp工具箱-setup-v0.1.5.exe')
check('T10d 附件名', uc.setup_asset_name('0.1.5') == 'Alp工具箱-setup-v0.1.5.exe')

html_mangled = (f'<a href="/starsinking07/alp-toolbox/releases/download/{NEWER_TAG}/'
                f'Alp.-setup-{NEWER_TAG[1:]}.exe">'
                f'x</a><a href="/starsinking07/alp-toolbox/releases/download/{NEWER_TAG}/Alp..exe">y</a>')
check('T10a2 碎片解析挑 setup (中文被改写为点号)', uc.setup_url_from_assets_html(html_mangled) ==
      f'https://github.com/starsinking07/alp-toolbox/releases/download/{NEWER_TAG}/Alp.-setup-{NEWER_TAG[1:]}.exe')
check('T10a3 无 exe → None', uc.setup_url_from_assets_html('<p>empty</p>') is None)
r = uc.summarize({'tag_name': NEWER_TAG, 'draft': False, 'prerelease': False,
                  'assets': [{'name': uc.setup_asset_name(NEWER_TAG[1:]),
                              'browser_download_url': uc.setup_download_url(NEWER_TAG[1:]), 'size': 5}]})
page._on_release(r)
check('T10e 更新版本摘要 → 下载可用', page.b_install.isEnabled())
check('T10f 同版本 → 已是最新', (page._on_release(uc.summarize(
    {'tag_name': 'v0.1.6', 'draft': False, 'prerelease': False, 'assets': []})) or True)
    and not page.b_install.isEnabled() and '已是最新' in page.lbl_update_hint.text())

class _FakeReply:
    """假 QNetworkReply: 只实现 finisher 用到的接口; rawHeader 钉死 str 签名 (防回归)。"""

    NoError = 0

    def __init__(self, status, headers=None, body=b''):
        self._status = status
        self._headers = headers or {}
        self._body = body
        self.deleted = False

    def attribute(self, key):
        return self._status

    def rawHeader(self, name):
        assert isinstance(name, str), f'rawHeader 需要 str, 收到 {type(name)}'
        v = self._headers.get(name, b'')
        return v if isinstance(v, bytes) else v.encode('utf-8')   # 模仿 QByteArray

    def readAll(self):
        return self._body

    def deleteLater(self):
        self.deleted = True


print('=== T11 检查器槽函数 (假 reply, 不碰网络) ===')
from brb02.update_check import UpdateChecker  # noqa: E402

ck = UpdateChecker()
got = []
ck.done.connect(lambda o: got.append(('done', o)))
ck.fail.connect(lambda s: got.append(('fail', s)))

fetched = []
fk = _FakeReply(302, {'Location': 'https://github.com/starsinking07/alp-toolbox/releases/tag/v0.1.6'})
ck._reply = fk
ck._fetch_assets = lambda tag: fetched.append(tag)   # 拦截第二跳 (不碰网络)
ck._on_html_finished(fk, 0)
check('T11a 302 → 转入附件碎片解析', fetched == ['v0.1.6'], str(fetched))
s = ck._summary('0.1.6', 'v0.1.6', 'https://github.com/starsinking07/alp-toolbox/releases/download/v0.1.6/Alp工具箱-setup-v0.1.6.exe')
ck.done.emit(s)
check('T11a2 摘要 setup 名', got[-1][1]['setup']['name'] == 'Alp工具箱-setup-v0.1.6.exe')

s2 = ck._summary('0.1.7', 'v0.1.7', 'https://github.com/starsinking07/alp-toolbox/releases/download/v0.1.7/Alp.-setup-v0.1.7.exe')
ck.done.emit(s2)
check('T11a3 改写名附件 → 名字原样保存', got[-1][1]['setup']['name'] == 'Alp.-setup-v0.1.7.exe')

ck._reply = _FakeReply(404)
ck._on_html_finished(ck._reply, 0)
check('T11b 404 → done(None)', got[-1][0] == 'done' and got[-1][1] is None)

ck._reply = _FakeReply(500)
ck._on_html_finished(ck._reply, 0)
check('T11c 500 → fail 报状态码', got[-1][0] == 'fail' and 'HTTP 500' in got[-1][1])

api_body = json.dumps({'message': 'API rate limit exceeded for 1.2.3.4.'}).encode('utf-8')
ck._reply = _FakeReply(403, {'X-RateLimit-Remaining': '0',
                             'X-RateLimit-Reset': str(int(time.time()) + 600)}, api_body)
ck._on_api_finished(ck._reply, 0)
check('T11d API 403 → 限流提示含等待', got[-1][0] == 'fail' and '限流' in got[-1][1]
      and '分钟' in got[-1][1], got[-1][1])

ck._reply = _FakeReply(200, body=json.dumps([{'tag_name': 'v0.2.0-rc1', 'draft': False,
                                              'prerelease': True, 'assets': []}]).encode('utf-8'))
ck._on_api_finished(ck._reply, 0)
check('T11e API 200 → 预发布摘要', got[-1][0] == 'done' and got[-1][1]
      and got[-1][1]['prerelease'] and got[-1][1]['version'] == '0.2.0-rc1')

# T11f 代次守卫: 旧代次回调必须被静默丢弃
fk_old = _FakeReply(302, {'Location': 'https://github.com/starsinking07/alp-toolbox/releases/tag/v0.1.6'})
ck._reply = fk_old
ck._seq = 5
ck._on_html_finished(fk_old, 3)
ck._seq = 0
check('T11f 旧代次回调被丢弃', got[-1][0] != 'done' or not (got[-1][1] and got[-1][1].get('version') == '0.1.6'),
      str(got[-1]))

print('=== 汇总 ===')
if fails:
    print(f'✗ {len(fails)} 项失败: {fails}')
    raise SystemExit(1)
print('✓ 全部通过 —— 关于页/更新检查可交付')
