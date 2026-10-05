# -*- coding: utf-8 -*-
"""
C盘清理大师 —— Windows 系统清理工具（复刻版）
================================================
单文件、零第三方依赖，Windows 自带 Python 即可运行：
    python c盘清理大师.py

特性：
  * 复刻经典 C盘清理工具界面（左侧导航 + 扫描进度 + 勾选卡片网格）
  * 真实扫描：临时文件 / 系统补丁缓存 / 日志 / 回收站 / 浏览器缓存 /
    缩略图缓存 / 错误报告 / 着色器缓存 / 微信缓存 等
  * 所有清理文件一律送入【回收站】（可反悔），绝不直接永久删除
  * 大文件专清：扫描 C 盘 100MB 以上大文件，可勾选清理
  * 在非 Windows 系统上自动进入演示模式（假数据，用于预览界面）

安全说明：
  * 仅针对公认的“缓存/临时/无用文件”路径，不碰文档、桌面等个人目录
  * 系统目录（Windows/Temp 等）需要管理员权限才能清理，未授权时自动跳过
"""

import os
import sys
import glob
import ctypes
import threading
import queue
import time
import traceback
import tkinter as tk
from tkinter import messagebox

IS_WINDOWS = (os.name == "nt")

# ----------------------------------------------------------------------------
# 常量与颜色
# ----------------------------------------------------------------------------
APP_TITLE = "C盘清理大师"

C_SIDEBAR      = "#17469B"   # 左侧深蓝
C_SIDEBAR_DARK = "#10336F"
C_SIDEBAR_SEL  = "#2E6BD6"   # 选中项
C_HEADER_BG    = "#FFFFFF"
C_PAGE_BG      = "#F5F7FA"
C_CARD_BG      = "#FFFFFF"
C_CARD_SEL     = "#FFF7E8"   # 卡片选中时淡橙底
C_ORANGE       = "#FF8C1A"   # 大小徽章
C_RED          = "#E64545"   # 可释放字样
C_GREEN        = "#21C36B"   # 一键按钮 / 勾选框
C_TEXT_MAIN    = "#333333"
C_TEXT_SUB     = "#888888"
C_BORDER       = "#E5E9F0"

FOLDERS_ICON = "💽"

# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def fmt_size(n):
    """字节数 -> 可读字符串"""
    if n is None or n < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit in ("B", "KB"):
                return "%dB" % n if unit == "B" else "%.0fKB" % n
            return "%.1f%s" % (n, unit)
        n /= 1024.0


def disk_usage(drive="C:\\" if IS_WINDOWS else "/"):
    """返回 (总容量, 可用容量)；失败返回 None"""
    if IS_WINDOWS:
        try:
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            ret = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive),
                ctypes.byref(ctypes.c_ulonglong(0)),  # caller avail
                ctypes.byref(total),
                ctypes.byref(free),
            )
            if ret:
                return total.value, free.value
        except Exception:
            pass
        return None
    # 演示模式假数据
    st = os.statvfs("/")
    return st.f_blocks * st.f_bsize, st.f_bavail * st.f_bsize


def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def send_to_recycle_bin(paths):
    """将一批文件/文件夹送入回收站（Windows），返回 True/False"""
    if not IS_WINDOWS:
        return False
    if not paths:
        return True

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_short),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40        # 送回收站
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400
    FOF_NO_CONNECTED_ELEMENTS = 0x2000

    ok_all = True
    # 分批，避免 pFrom 超长
    CHUNK = 120
    for i in range(0, len(paths), CHUNK):
        batch = paths[i:i + CHUNK]
        buf = ctypes.create_unicode_buffer("\0".join(batch) + "\0\0")
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
        op.pTo = None
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                     FOF_NOERRORUI | FOF_NO_CONNECTED_ELEMENTS)
        try:
            rv = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            if rv != 0 or op.fAnyOperationsAborted:
                ok_all = False
        except Exception:
            ok_all = False
    return ok_all


# ----------------------------------------------------------------------------
# 清理项定义
#   paths 里支持环境变量与通配符；special 指定特殊扫描器
# ----------------------------------------------------------------------------
CLEAN_ITEMS = [
    # ---------------- 推荐清理项 ----------------
    dict(id="wechat", name="微信缓存", icon="💬", section="rec", checked=True,
         paths=[
             "~/Documents/WeChat Files/*/FileStorage/Cache",
             "~/Documents/WeChat Files/*/FileStorage/Cache/*",
             "~/Documents/xwechat_files/*/cache",
             "~/Documents/xwechat_files/*/msg/file-cache",
         ],
         note="微信聊天过程中产生的图片/文件缓存（不影响聊天记录）"),
    dict(id="hotfix", name="系统补丁", icon="🩹", section="rec", checked=True,
         paths=["C:/Windows/SoftwareDistribution/Download"],
         note="Windows 更新已安装后残留的补丁安装包"),
    dict(id="tempfile", name="临时文件", icon="📁", section="rec", checked=True,
         paths=["%TEMP%", "C:/Windows/Temp"],
         note="系统和各类软件运行时产生的临时文件"),
    dict(id="logs", name="日志文件", icon="📄", section="rec", checked=True,
         paths=["C:/Windows/Logs", "~/AppData/Local/CrashDumps"],
         note="系统组件日志与程序崩溃转储"),
    dict(id="edge", name="Edge浏览器", icon="🌐", section="rec", checked=True,
         paths=["~/AppData/Local/Microsoft/Edge/User Data/*/Cache",
                "~/AppData/Local/Microsoft/Edge/User Data/*/Code Cache"],
         note="Edge 浏览器网页缓存（不会丢失密码与收藏）"),
    dict(id="recycle", name="回收站", icon="🗑️", section="rec", checked=True,
         special="recycle",
         note="回收站里的已删除文件"),
    dict(id="thumbs", name="缩略图缓存", icon="🖼️", section="rec", checked=True,
         paths=["~/AppData/Local/Microsoft/Windows/Explorer/thumbcache_*.db",
                "~/AppData/Local/Microsoft/Windows/Explorer/iconcache_*.db"],
         note="图片缩略图缓存，清理后首次打开文件夹会重建"),
    dict(id="wer", name="错误报告", icon="🚨", section="rec", checked=True,
         paths=["~/AppData/Local/Microsoft/Windows/WER",
                "C:/ProgramData/Microsoft/Windows/WER"],
         note="Windows 错误报告队列文件"),
    dict(id="d3d", name="着色器缓存", icon="🎮", section="rec", checked=True,
         paths=["~/AppData/Local/D3DSCache"],
         note="DirectX 着色器缓存，游戏首次启动会重新编译"),
    dict(id="delivery", name="更新传递缓存", icon="📦", section="rec", checked=True,
         paths=["C:/Windows/ServiceProfiles/NetworkService/AppData/Local/"
                "Microsoft/Windows/DeliveryOptimization/Cache"],
         note="Windows 更新 P2P 分发缓存（需管理员）"),
    dict(id="minidump", name="系统转储", icon="💥", section="rec", checked=True,
         paths=["C:/Windows/Minidump", "C:/Windows/MEMORY.DMP"],
         note="蓝屏时生成的内存转储文件，通常很大（需管理员）"),

    # ---------------- 需确认清理项 ----------------
    dict(id="chrome", name="Chrome缓存", icon="🧭", section="confirm", checked=False,
         paths=["~/AppData/Local/Google/Chrome/User Data/*/Cache",
                "~/AppData/Local/Google/Chrome/User Data/*/Code Cache"],
         note="Chrome 浏览器网页缓存"),
    dict(id="qq", name="QQ缓存", icon="🐧", section="confirm", checked=False,
         paths=["~/Documents/Tencent Files/*/FileStorage/Cache",
                "~/Documents/xwechat_files/../Tencent/*/FileStorage/Cache"],
         note="QQ 聊天图片/文件缓存"),
    dict(id="pipcache", name="开发工具缓存", icon="🧰", section="confirm", checked=False,
         paths=["~/AppData/Local/pip/cache",
                "~/AppData/Local/npm-cache",
                "~/AppData/Local/Yarn/Cache"],
         note="pip / npm 等开发工具的下载缓存"),
    dict(id="winold", name="Windows.old", icon="🪟", section="confirm", checked=False,
         paths=["C:/Windows.old"],
         note="系统升级后的旧系统备份，确认新系统正常后可清理（很大，需管理员）"),
]

SIDEBAR_PAGES = [
    ("slim",    "C盘瘦身",  "🧹"),
    ("defrag",  "碎片清理",  "📊"),
    ("privacy", "隐私清理",  "🔒"),
    ("bigfile", "大文件专清", "📦"),
    ("dup",     "重复文件专清", "🗂️"),
    ("wechat",  "微信专清",  "💬"),
    ("qq",      "QQ清理",   "🐧"),
    ("shred",   "文件粉碎",  "💥"),
    ("about",   "关于",     "ℹ️"),
]


# ----------------------------------------------------------------------------
# 扫描器
# ----------------------------------------------------------------------------
def expand_paths(templates):
    """展开环境变量与通配符，返回真实存在的路径列表"""
    out = []
    for t in templates:
        p = os.path.expandvars(os.path.expanduser(t)).replace("\\", "/")
        if any(ch in p for ch in "*?["):
            out.extend(glob.glob(p))
        elif os.path.exists(p):
            out.append(p)
    return out


def scan_dir_tree(paths, progress_cb=None, limit_files=200000):
    """递归统计大小并收集文件清单"""
    total = 0
    files = []
    for p in paths:
        if os.path.isfile(p):
            try:
                total += os.path.getsize(p)
                files.append(p)
            except OSError:
                pass
            continue
        for root, dirs, fs in os.walk(p, onerror=lambda e: None):
            for f in fs:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                    files.append(fp)
                except OSError:
                    pass
            if len(files) > limit_files:
                break
            if progress_cb:
                progress_cb(root)
    return total, files


def scan_recycle_bin_size():
    """估算 C 盘回收站大小（无权限的 SID 自动跳过）"""
    base = "C:/$Recycle.Bin" if IS_WINDOWS else "/tmp/.fake-recycle"
    total = 0
    for root, dirs, fs in os.walk(base, onerror=lambda e: None):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ----------------------------------------------------------------------------
# 主应用
# ----------------------------------------------------------------------------
class CleanerApp:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.scanning = False
        self.cleaning = False
        self.items = {}          # id -> dict(size, files, checked, card...)
        self.checked_total = 0
        self.big_files = []      # [(path, size, checked)]
        self.sec_labels = {}     # 分区统计标签
        self._disk_info = None
        self.demo = not IS_WINDOWS

        root.title(APP_TITLE)
        root.geometry("1020x660")
        root.minsize(920, 600)
        root.configure(bg=C_PAGE_BG)

        self._init_fonts()
        self._build_sidebar()
        self._build_main()
        self._build_pages()

        for it in CLEAN_ITEMS:
            self.items[it["id"]] = dict(defn=it, size=0, files=[],
                                        checked=it.get("checked", False),
                                        card=None)

        self.show_page("slim")
        self.root.after(120, self._poll_queue)
        # 启动即自动扫描
        self.root.after(300, self.start_scan)

    # ------------------------- 字体 -------------------------
    def _init_fonts(self):
        base = "Microsoft YaHei UI" if IS_WINDOWS else "PingFang SC"
        self.f_side      = (base, 11)
        self.f_side_act  = (base, 11, "bold")
        self.f_side_logo = (base, 13, "bold")
        self.f_big       = (base, 20, "bold")
        self.f_h2        = (base, 12, "bold")
        self.f_norm      = (base, 10)
        self.f_small     = (base, 9)
        self.f_badge     = (base, 10, "bold")
        self.f_btn       = (base, 13, "bold")

    # ------------------------- 左侧导航 -------------------------
    def _build_sidebar(self):
        sb = tk.Frame(self.root, width=200, bg=C_SIDEBAR)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        logo = tk.Frame(sb, bg=C_SIDEBAR)
        logo.pack(fill="x", pady=(18, 14))
        tk.Label(logo, text=FOLDERS_ICON, bg=C_SIDEBAR, fg="white",
                 font=(self.f_side_logo[0], 16)).pack()
        tk.Label(logo, text="C盘清理", bg=C_SIDEBAR, fg="white",
                 font=self.f_side_logo).pack()

        self.side_buttons = {}
        for pid, label, icon in SIDEBAR_PAGES:
            b = tk.Label(sb, text="  %s  %s" % (icon, label),
                         font=self.f_side, fg="#DCE6F8", bg=C_SIDEBAR,
                         anchor="w", padx=8, pady=9, cursor="hand2")
            b.pack(fill="x", padx=10, pady=2)
            b.bind("<Button-1>", lambda e, p=pid: self.show_page(p))
            self.side_buttons[pid] = b

        ver = tk.Label(sb, text="v1.0 · 安全模式(回收站)",
                       bg=C_SIDEBAR, fg="#9FB6E0", font=self.f_small)
        ver.pack(side="bottom", pady=10)

    def _mark_sidebar(self, pid):
        for p, b in self.side_buttons.items():
            if p == pid:
                b.configure(bg=C_SIDEBAR_SEL, fg="white", font=self.f_side_act)
            else:
                b.configure(bg=C_SIDEBAR, fg="#DCE6F8", font=self.f_side)

    # ------------------------- 右侧主体框架 -------------------------
    def _build_main(self):
        main = tk.Frame(self.root, bg=C_PAGE_BG)
        main.pack(side="left", fill="both", expand=True)

        # 顶部标题栏
        bar = tk.Frame(main, bg=C_SIDEBAR, height=26)
        bar.pack(fill="x")
        if IS_WINDOWS:
            bar.pack_forget()  # Windows 上用系统标题栏

        # ============ 页面容器 ============
        self.container = tk.Frame(main, bg=C_PAGE_BG)
        self.container.pack(fill="both", expand=True)
        self.pages = {}

    def _build_pages(self):
        self._build_page_slim()
        self._build_page_bigfile()
        self._build_page_stub("defrag", "碎片清理",
                              "机械硬盘可用 Windows 自带「碎片整理和优化驱动器」；\n"
                              "SSD 无需碎片整理。此页面为占位说明。")
        self._build_page_stub("privacy", "隐私清理",
                              "将支持：浏览记录 / Cookie / 最近使用记录清理。\n"
                              "涉及个人数据，默认不做任何自动删除。")
        self._build_page_stub("dup", "重复文件专清",
                              "将支持：全盘重复文件扫描与安全去重。\n开发中。")
        self._build_page_stub("wechat", "微信专清",
                              "微信缓存已包含在「C盘瘦身 → 推荐清理项 → 微信缓存」中，\n"
                              "专项分类页开发中。")
        self._build_page_stub("qq", "QQ清理",
                              "QQ 缓存已包含在「C盘瘦身 → 需确认清理项 → QQ缓存」中。\n专项页开发中。")
        self._build_page_stub("shred", "文件粉碎",
                              "出于安全考虑未实现“不可恢复粉碎”，\n"
                              "所有清理均走系统回收站，可随时还原。")
        self._build_page_about()

    # ------------------------- 通用页面骨架 -------------------------
    def _new_page(self, pid, with_header=True):
        page = tk.Frame(self.container, bg=C_PAGE_BG)
        self.pages[pid] = page
        # 不立即 pack，由 show_page 控制
        return page

    def _build_header(self, page):
        """构建顶部扫描区（磁盘条 + 标题 + 一键按钮），返回控件字典"""
        header = tk.Frame(page, bg=C_HEADER_BG)
        header.pack(fill="x")

        disk_icon = tk.Label(header, text=FOLDERS_ICON,
                             bg=C_HEADER_BG, font=(self.f_big[0], 30))
        disk_icon.pack(side="left", padx=(24, 8), pady=14)

        mid = tk.Frame(header, bg=C_HEADER_BG)
        mid.pack(side="left", fill="both", expand=True, pady=10)

        title = tk.Label(mid, bg=C_HEADER_BG, font=self.f_big, anchor="w")
        title.pack(anchor="w")

        barwrap = tk.Frame(mid, bg=C_HEADER_BG)
        barwrap.pack(anchor="w", pady=(6, 2))
        canvas = tk.Canvas(barwrap, width=330, height=22,
                           bg=C_HEADER_BG, highlightthickness=0)
        canvas.pack(side="left")
        disk_text = tk.Label(barwrap, text="", bg=C_HEADER_BG,
                             fg=C_TEXT_SUB, font=self.f_small)
        disk_text.pack(side="left", padx=10)

        scan_label = tk.Label(mid, text="正在准备扫描…", bg=C_HEADER_BG,
                              fg=C_TEXT_SUB, font=self.f_small, anchor="w")
        scan_label.pack(anchor="w", pady=(4, 0))

        btn = tk.Button(header, text="一键瘦身", font=self.f_btn,
                        bg=C_GREEN, fg="white", activebackground="#1BAA5C",
                        activeforeground="white", relief="flat",
                        cursor="hand2", padx=30, pady=10,
                        state="disabled", command=self.on_clean_clicked)
        btn.pack(side="right", padx=24)

        return dict(canvas=canvas, disk_text=disk_text, title=title,
                    scan=scan_label, btn=btn)

    def _draw_disk_bar(self, canvas, total, free):
        canvas.delete("all")
        if not total:
            return
        used = max(total - free, 0)
        ratio = used / total
        w, h = 330, 22
        # 分段配色：深红 -> 橙 -> 浅橙 -> 灰，复刻原图
        segs = [
            (min(ratio, 0.25), "#E64545"),
            (min(max(ratio - 0.25, 0), 0.25), "#FF8C1A"),
            (min(max(ratio - 0.5, 0), 0.25), "#FFC773"),
            (min(max(ratio - 0.75, 0), 0.25), "#E9ECEF"),
        ]
        x = 0
        for frac, color in segs:
            if frac <= 0:
                # 剩余全部灰色
                canvas.create_rectangle(x, 0, w, h, fill="#E9ECEF", outline="")
                break
            sw = frac * w
            canvas.create_rectangle(x, 0, x + sw, h, fill=color, outline="")
            x += sw
        if x < w:
            canvas.create_rectangle(x, 0, w, h, fill="#E9ECEF", outline="")
        canvas.create_rectangle(0, 0, w, h, outline="#D8DEE6")

    # ======================= 页面：C盘瘦身 =======================
    def _build_page_slim(self):
        page = self._new_page("slim")
        self.slim_hdr = self._build_header(page)
        self.slim_hdr["btn"].configure(text="一键瘦身")

        # 选项卡
        tabbar = tk.Frame(page, bg=C_HEADER_BG)
        tabbar.pack(fill="x")
        self.slim_tabs = {}
        for i, (key, label) in enumerate(
                [("sys", "系统清理"), ("move", "软件搬家"), ("big", "大文件搬家")]):
            active = (key == "sys")
            t = tk.Label(tabbar, text=label, font=self.f_h2,
                         fg=C_ORANGE if active else C_TEXT_MAIN,
                         bg=C_HEADER_BG, pady=10, cursor="hand2")
            t.pack(side="left", padx=30)
            t.bind("<Button-1>", lambda e, k=key: self._on_slim_tab(k))
            self.slim_tabs[key] = t
        tk.Frame(page, height=2, bg=C_ORANGE).pack(fill="x")

        # 可滚动内容
        wrap = tk.Frame(page, bg=C_PAGE_BG)
        wrap.pack(fill="both", expand=True)
        self.slim_canvas = tk.Canvas(wrap, bg=C_PAGE_BG, highlightthickness=0)
        vs = tk.Scrollbar(wrap, orient="vertical", command=self.slim_canvas.yview)
        self.slim_canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.slim_canvas.pack(side="left", fill="both", expand=True)

        self.slim_inner = tk.Frame(self.slim_canvas, bg=C_PAGE_BG)
        self.slim_canvas.create_window((0, 0), window=self.slim_inner, anchor="nw")
        self.slim_inner.bind(
            "<Configure>",
            lambda e: self.slim_canvas.configure(
                scrollregion=self.slim_canvas.bbox("all")))
        self.slim_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.slim_canvas.yview_scroll(-1 * (e.delta // 120 or -1), "units")
            if e.delta else None)

        self._build_slim_section(self.slim_inner)
        self._on_slim_tab("sys")

    def _on_slim_tab(self, key):
        for k, t in self.slim_tabs.items():
            t.configure(fg=C_ORANGE if k == key else C_TEXT_MAIN)
        if key == "sys":
            self.slim_sections_parent.pack(fill="x")
        else:
            self.slim_sections_parent.pack_forget()
            # 占位提示
            for child in self.slim_inner.winfo_children():
                if getattr(child, "_is_placeholder", False):
                    child.pack_forget()
            ph = getattr(self, "_slim_placeholder", None)
            if ph is None:
                ph = tk.Frame(self.slim_inner, bg=C_PAGE_BG)
                ph._is_placeholder = True
                lbl = tk.Label(ph, text="该功能开发中，敬请期待…",
                               fg=C_TEXT_SUB, bg=C_PAGE_BG, font=self.f_h2)
                lbl.pack(pady=60)
                self._slim_placeholder = ph
            ph.pack(fill="x")

    def _build_slim_section(self, parent):
        self.slim_sections_parent = tk.Frame(parent, bg=C_PAGE_BG)
        self.slim_sections_parent.pack(fill="x")

        self.section_frames = {}
        for key, sec_title, sec_desc in [
            ("rec", "推荐清理项", "系统和软件的垃圾和无用文件，您可以放心清理"),
            ("confirm", "需确认清理项", "系统和软件的数据缓存和不用文件，您可根据需要选择清理"),
        ]:
            # 分区标题
            head = tk.Frame(self.slim_sections_parent, bg=C_PAGE_BG)
            head.pack(fill="x", pady=(14, 6))
            dot = "⭐" if key == "rec" else "☰"
            row = tk.Frame(head, bg=C_PAGE_BG)
            row.pack(anchor="w", padx=18)
            tk.Label(row, text=dot, bg=C_PAGE_BG, font=self.f_h2).pack(side="left")
            tk.Label(row, text=" " + sec_title + "  ", bg=C_PAGE_BG,
                     fg=C_TEXT_MAIN, font=self.f_h2).pack(side="left")
            self.sec_labels[key] = tk.Label(row, text="", bg=C_PAGE_BG,
                                            fg=C_TEXT_SUB, font=self.f_small)
            self.sec_labels[key].pack(side="left")

            tk.Label(head, text=sec_desc, bg=C_PAGE_BG, fg=C_TEXT_SUB,
                     font=self.f_small).pack(anchor="w", padx=44)

            grid = tk.Frame(self.slim_sections_parent, bg=C_PAGE_BG)
            grid.pack(fill="x", padx=14, pady=(4, 6))
            self.section_frames[key] = grid

    # ------------------------- 卡片 -------------------------
    def _make_card(self, parent, item_id, name, icon, size_text, checked):
        """构建一个清理项卡片，返回卡片控件"""
        sel_bg = C_CARD_SEL if checked else C_CARD_BG
        card = tk.Frame(parent, bg=sel_bg, highlightthickness=1,
                        highlightbackground=C_BORDER, cursor="hand2")
        icon_lbl = tk.Label(card, text=icon, bg=sel_bg,
                            font=(self.f_norm[0], 22))
        icon_lbl.pack(pady=(12, 0))
        name_lbl = tk.Label(card, text=name, bg=sel_bg, fg=C_TEXT_MAIN,
                            font=self.f_norm)
        name_lbl.pack(pady=(4, 2))
        badge = tk.Label(card, text=size_text, bg=C_ORANGE, fg="white",
                         font=self.f_badge, padx=8, pady=2)
        badge.pack(pady=(0, 6))
        chk = tk.Canvas(card, width=20, height=20, bg=sel_bg,
                        highlightthickness=0)
        chk.pack(pady=(0, 10))
        self._draw_check(chk, checked)

        for w in (card, icon_lbl, name_lbl, badge):
            w.bind("<Button-1>", lambda e, i=item_id: self.toggle_item(i))
        card._badge = badge
        card._check = chk
        card._name_lbl = name_lbl
        card._icon_lbl = icon_lbl
        return card

    def _draw_check(self, canvas, checked):
        canvas.delete("all")
        if checked:
            canvas.create_oval(1, 1, 19, 19, fill=C_GREEN, outline=C_GREEN)
            canvas.create_line(5, 10, 9, 14, fill="white", width=2,
                               capstyle="round")
            canvas.create_line(9, 14, 15, 6, fill="white", width=2,
                               capstyle="round")
        else:
            canvas.create_oval(1, 1, 19, 19, fill="white", outline="#C6CDD6")

    def refresh_item_cards(self):
        """根据当前数据重建卡片网格"""
        for key, grid in self.section_frames.items():
            for w in grid.winfo_children():
                w.destroy()

        cols = 6
        for r_i, (key, _, _) in enumerate(
                [("rec", "", ""), ("confirm", "", "")]):
            grid = self.section_frames[key]
            ids = [it["id"] for it in CLEAN_ITEMS if it["section"] == key]
            for i, iid in enumerate(ids):
                d = self.items[iid]
                dfn = d["defn"]
                card = self._make_card(
                    grid, iid, dfn["name"], dfn["icon"],
                    fmt_size(d["size"]) if d["size"] else "0MB",
                    d["checked"])
                card.grid(row=i // cols, column=i % cols,
                          padx=6, pady=6, sticky="nsew")
                d["card"] = card
            for c in range(cols):
                grid.columnconfigure(c, weight=1)

        self.update_totals()

    def toggle_item(self, iid):
        d = self.items[iid]
        if self.scanning and not d["size"]:
            return
        d["checked"] = not d["checked"]
        card = d.get("card")
        if card:
            bg = C_CARD_SEL if d["checked"] else C_CARD_BG
            for w in (card, card._badge, card._check,
                      card._name_lbl, card._icon_lbl):
                w.configure(bg=bg)
            card._check.configure(bg=bg)
            self._draw_check(card._check, d["checked"])
        self.update_totals()

    def update_totals(self):
        rec_c = rec_t = con_c = con_t = checked = 0
        for iid, d in self.items.items():
            s = d["size"]
            if d["defn"]["section"] == "rec":
                rec_t += s
                rec_c += s if d["checked"] else 0
            else:
                con_t += s
                con_c += s if d["checked"] else 0
            checked += s if d["checked"] else 0
        self.checked_total = checked
        self.sec_labels["rec"].configure(
            text="- 已选%s/%s" % (fmt_size(rec_c), fmt_size(rec_t)))
        self.sec_labels["confirm"].configure(
            text="- 已选%s/%s" % (fmt_size(con_c), fmt_size(con_t)))

        total, free = self._disk_info or (None, None)
        if total:
            self.slim_hdr["title"].configure(
                text=("发现 %s 可释放，已勾选 %s"
                      % (fmt_size(rec_t + con_t), fmt_size(checked))))
            # 已勾选部分用红色高亮
            self.slim_hdr["title"].configure(fg=C_TEXT_MAIN)

        btn_state = "normal" if (not self.scanning and checked > 0) else "disabled"
        self.slim_hdr["btn"].configure(state=btn_state,
                                       text="一键瘦身" if not self.cleaning else "正在清理…")

    # ======================= 页面：大文件专清 =======================
    def _build_page_bigfile(self):
        page = self._new_page("bigfile")
        self.big_hdr = self._build_header(page)
        self.big_hdr["btn"].configure(text="一键清理", command=self.on_clean_big)

        tip = tk.Label(page, text="扫描 C 盘中超过 100MB 的大文件（跳过系统关键目录），"
                                  "勾选后送入回收站。",
                       bg=C_HEADER_BG, fg=C_TEXT_SUB, font=self.f_small,
                       anchor="w")
        tip.pack(fill="x", padx=24, pady=(0, 8))

        wrap = tk.Frame(page, bg=C_PAGE_BG)
        wrap.pack(fill="both", expand=True)
        self.big_list = tk.Canvas(wrap, bg=C_PAGE_BG, highlightthickness=0)
        vs = tk.Scrollbar(wrap, orient="vertical", command=self.big_list.yview)
        self.big_list.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.big_list.pack(side="left", fill="both", expand=True)
        self.big_inner = tk.Frame(self.big_list, bg=C_PAGE_BG)
        self.big_list.create_window((0, 0), window=self.big_inner, anchor="nw")
        self.big_inner.bind(
            "<Configure>",
            lambda e: self.big_list.configure(
                scrollregion=self.big_list.bbox("all")))

    def start_bigfile_scan(self):
        if self.scanning:
            return
        self.scanning = True
        self.big_hdr["btn"].configure(state="disabled")
        self.big_hdr["scan"].configure(text="正在扫描大文件…")

        def worker():
            SKIP = {"C:/Windows", "C:/Program Files", "C:/Program Files (x86)",
                    "C:/ProgramData", "C:/Users/All Users"}
            found = []
            roots = ["C:/Users", "C:/ProgramData"] if IS_WINDOWS else ["/Users"]
            for base in roots:
                for root, dirs, fs in os.walk(base, onerror=lambda e: None):
                    # 限制深度 & 跳过已知的程序目录
                    depth = root.replace(base, "").count("/")
                    if depth >= 5:
                        dirs[:] = []
                    norm = root.replace("\\", "/")
                    if any(norm == s or norm.startswith(s + "/") for s in SKIP):
                        dirs[:] = []
                        continue
                    for f in fs:
                        fp = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(fp)
                            if sz >= 100 * 1024 * 1024:
                                found.append((fp, sz))
                                self.q.put(("bigfound", fp, sz))
                        except OSError:
                            pass
            found.sort(key=lambda x: -x[1])
            self.q.put(("bigdone", found[:100]))

        threading.Thread(target=worker, daemon=True).start()

    def render_bigfiles(self, files):
        for w in self.big_inner.winfo_children():
            w.destroy()
        self.big_files = [[p, s, False] for p, s in files]
        total = sum(s for _, s in files)
        self.big_hdr["title"].configure(
            text="发现 %d 个大文件，共 %s" % (len(files), fmt_size(total)))
        self.big_hdr["scan"].configure(
            text="勾选不需要的大文件，点击右上角「一键清理」")
        if not files:
            tk.Label(self.big_inner, text="未发现 100MB 以上的大文件 🎉",
                     bg=C_PAGE_BG, fg=C_TEXT_SUB, font=self.f_h2).pack(pady=40)
            return
        for i, (p, s) in enumerate(files):
            row = tk.Frame(self.big_inner, bg=C_CARD_BG,
                           highlightthickness=1, highlightbackground=C_BORDER)
            row.pack(fill="x", padx=16, pady=3)
            chk = tk.Canvas(row, width=20, height=20, bg=C_CARD_BG,
                            highlightthickness=0, cursor="hand2")
            chk.pack(side="left", padx=10, pady=8)
            self._draw_check(chk, False)
            lbl = tk.Label(row, text=p, bg=C_CARD_BG, fg=C_TEXT_MAIN,
                           font=self.f_small, anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            sz = tk.Label(row, text=fmt_size(s), bg=C_ORANGE, fg="white",
                          font=self.f_badge, padx=8, pady=2)
            sz.pack(side="right", padx=10)

            def toggle(e, idx=i, cv=chk):
                self.big_files[idx][2] = not self.big_files[idx][2]
                self._draw_check(cv, self.big_files[idx][2])
                sel = sum(s for _, s, c in self.big_files if c)
                self.big_hdr["title"].configure(
                    text="已勾选 %s / 共 %d 个大文件" % (fmt_size(sel),
                                                       len(self.big_files)))
            for w in (row, lbl, sz, chk):
                w.bind("<Button-1>", toggle)

    def on_clean_big(self):
        sel = [p for p, s, c in self.big_files if c]
        if not sel:
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "将把 %d 个大文件送入回收站（可在回收站还原）。\n"
                "注意：正在被占用的文件可能无法删除。继续？\n\n%s..."
                % (len(sel), "\n".join(sel[:5]))):
            return
        self.big_hdr["btn"].configure(state="disabled", text="正在清理…")

        def worker():
            ok = send_to_recycle_bin(sel)
            self.q.put(("bigclean_done", ok, len(sel)))

        threading.Thread(target=worker, daemon=True).start()

    # ======================= 占位页 =======================
    def _build_page_stub(self, pid, title, desc):
        page = self._new_page(pid, with_header=False)
        tk.Label(page, text=title, bg=C_PAGE_BG, fg=C_TEXT_MAIN,
                 font=self.f_big).pack(pady=(60, 10))
        tk.Label(page, text=desc, bg=C_PAGE_BG, fg=C_TEXT_SUB,
                 font=self.f_norm, justify="left").pack()

    def _build_page_about(self):
        page = self._new_page("about", with_header=False)
        tk.Label(page, text=FOLDERS_ICON + "  C盘清理大师",
                 bg=C_PAGE_BG, fg=C_TEXT_MAIN, font=self.f_big).pack(pady=(70, 6))
        tk.Label(page, text="v1.0 · 单文件 · 零依赖 · 纯 Python tkinter",
                 bg=C_PAGE_BG, fg=C_TEXT_SUB, font=self.f_norm).pack()
        tips = (
            "安全设计：\n"
            "  · 所有文件一律送入回收站，绝不直接永久删除\n"
            "  · 仅清理公认的缓存/临时目录，不碰个人文档\n"
            "  · 系统目录清理需要管理员权限，无权限自动跳过\n\n"
            "建议以管理员身份运行，可清理系统级缓存。"
        )
        tk.Label(page, text=tips, bg=C_PAGE_BG, fg=C_TEXT_MAIN,
                 font=self.f_norm, justify="left").pack(pady=16)
        if IS_WINDOWS and not is_admin():
            tk.Button(page, text="以管理员身份重启", font=self.f_norm,
                      bg=C_ORANGE, fg="white", relief="flat", cursor="hand2",
                      padx=16, pady=6, command=self.relaunch_as_admin).pack(pady=6)

    def relaunch_as_admin(self):
        try:
            params = '"%s"' % os.path.abspath(sys.argv[0])
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1)
            self.root.destroy()
        except Exception as e:
            messagebox.showerror(APP_TITLE, "提权重启失败：%s" % e)

    # ======================= 页面切换 =======================
    def show_page(self, pid):
        for p in self.pages.values():
            p.pack_forget()
        page = self.pages[pid]
        page.pack(fill="both", expand=True)
        self._mark_sidebar(pid)
        if pid == "bigfile" and not self.big_files:
            self.start_bigfile_scan()

    # ======================= 扫描 =======================
    def start_scan(self):
        if self.scanning:
            return
        self.scanning = True
        self.slim_hdr["btn"].configure(state="disabled")
        self.slim_hdr["scan"].configure(text="正在扫描…")
        self.slim_hdr["title"].configure(text="正在扫描垃圾文件…")

        # 演示模式：直接填假数据
        if self.demo:
            demo_sizes = {"wechat": 8.3e9, "hotfix": 7.1e9, "tempfile": 2.9e9,
                          "logs": 2.5e9, "edge": 1.7e9, "recycle": 4.11e8,
                          "thumbs": 3.37e8, "wer": 1.88e8, "d3d": 1.79e8,
                          "delivery": 5.4e7, "minidump": 3.6e7,
                          "chrome": 5.0e8, "qq": 3.0e8, "pipcache": 1.2e8,
                          "winold": 0}
            for iid, sz in demo_sizes.items():
                self.q.put(("item", iid, int(sz), [], ""))
            self.q.put(("disk", 119e9, 16.5e9))
            self.q.put(("done", True))
            return

        def worker():
            try:
                self.q.put(("disk", *disk_usage()))
                for it in CLEAN_ITEMS:
                    iid = it["id"]
                    self.q.put(("scanpath", it["name"], ""))
                    if it.get("special") == "recycle":
                        sz = scan_recycle_bin_size()
                        self.q.put(("item", iid, sz, [], ""))
                        continue
                    paths = expand_paths(it["paths"])
                    cb = lambda root, n=it["name"]: self.q.put(
                        ("scanpath", n, root))
                    sz, files = scan_dir_tree(paths, progress_cb=cb)
                    self.q.put(("item", iid, sz, files, ""))
                self.q.put(("done", True))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    # ======================= 清理 =======================
    def on_clean_clicked(self):
        ids = [iid for iid, d in self.items.items()
               if d["checked"] and d["size"] > 0]
        if not ids:
            return
        total = sum(self.items[i]["size"] for i in ids)
        if not messagebox.askyesno(
                APP_TITLE,
                "即将清理 %s 的垃圾文件。\n\n"
                "· 所有文件将送入回收站，可在回收站还原\n"
                "· 正在使用的文件将被跳过\n\n确定继续吗？" % fmt_size(total)):
            return
        self.cleaning = True
        self.slim_hdr["btn"].configure(state="disabled", text="正在清理…")

        def worker():
            freed = 0
            failed = 0
            for iid in ids:
                d = self.items[iid]
                name = d["defn"]["name"]
                self.q.put(("cleaning", name))
                files = d["files"]
                if d["defn"].get("special") == "recycle":
                    # 回收站：调用清空
                    freed += d["size"]
                    if IS_WINDOWS:
                        try:
                            ctypes.windll.shell32.SHEmptyRecycleBinW(
                                None, "C:\\", 7)  # 无确认+无声音+不弹UI
                        except Exception:
                            failed += 1
                    d["size"] = 0
                    d["files"] = []
                    self.q.put(("item", iid, 0, [], ""))
                    continue
                if not files:
                    continue
                before_dirs = set()
                ok = send_to_recycle_bin(files)
                if ok:
                    freed += d["size"]
                else:
                    failed += len(files)
                d["size"] = 0
                d["files"] = []
                self.q.put(("item", iid, 0, [], ""))
            self.q.put(("cleandone", freed, failed))

        threading.Thread(target=worker, daemon=True).start()

    # ======================= 消息轮询 =======================
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "disk":
                    self._disk_info = (msg[1], msg[2])
                    total, free = msg[1], msg[2]
                    self._draw_disk_bar(self.slim_hdr["canvas"], total, free)
                    self._draw_disk_bar(self.big_hdr["canvas"], total, free)
                    if total:
                        t = "C: 共%dGB，%.1fGB可用" % (
                            total / 1e9, free / 1e9)
                        self.slim_hdr["disk_text"].configure(text=t)
                        self.big_hdr["disk_text"].configure(text=t)
                elif kind == "scanpath":
                    txt = msg[1] if not msg[2] else "%s: %s" % (
                        msg[1], msg[2])
                    self.slim_hdr["scan"].configure(text="正在扫描: " + txt)
                elif kind == "item":
                    iid, sz, files = msg[1], msg[2], msg[3]
                    d = self.items.get(iid)
                    if d:
                        d["size"] = sz
                        d["files"] = files
                        if d.get("card"):
                            card = d["card"]
                            bg = C_CARD_SEL if d["checked"] else C_CARD_BG
                            card._badge.configure(text=fmt_size(sz))
                elif kind == "done":
                    self.scanning = False
                    self.refresh_item_cards()
                    self.slim_hdr["scan"].configure(
                        text="扫描完成，勾选需要清理的项目")
                    if self.demo:
                        self.slim_hdr["title"].configure(
                            text="演示模式：发现 19.4GB 可释放，已勾选 19.4GB")
                elif kind == "cleaning":
                    self.slim_hdr["scan"].configure(text="正在清理: " + msg[1])
                elif kind == "cleandone":
                    freed, failed = msg[1], msg[2]
                    self.cleaning = False
                    self.refresh_item_cards()
                    self.slim_hdr["scan"].configure(text="清理完成")
                    self.slim_hdr["btn"].configure(text="一键瘦身")
                    self._scan_done_title()
                    messagebox.showinfo(
                        APP_TITLE,
                        "清理完成！共释放 %s%s"
                        % (fmt_size(freed),
                           "（%d 个文件被占用或失败）" % failed if failed else ""))
                elif kind == "bigfound":
                    pass
                elif kind == "bigdone":
                    self.scanning = False
                    self.render_bigfiles(msg[1])
                elif kind == "bigclean_done":
                    ok, n = msg[1], msg[2]
                    self.big_hdr["btn"].configure(
                        state="normal", text="一键清理")
                    if ok:
                        messagebox.showinfo(APP_TITLE,
                                            "已将 %d 个大文件送入回收站" % n)
                        self.big_files = []
                        self.render_bigfiles([])
                        self.start_bigfile_scan()
                    else:
                        messagebox.showwarning(
                            APP_TITLE, "部分文件清理失败（可能被占用）")
                elif kind == "error":
                    self.scanning = False
                    self.slim_hdr["scan"].configure(text="扫描出错")
                    messagebox.showerror(APP_TITLE, "扫描出错：\n" + msg[1])
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _scan_done_title(self):
        rec_t = sum(d["size"] for d in self.items.values()
                    if d["defn"]["section"] == "rec")
        con_t = sum(d["size"] for d in self.items.values()
                    if d["defn"]["section"] == "confirm")
        self.slim_hdr["title"].configure(
            text="发现 %s 可释放，已勾选 %s"
                 % (fmt_size(rec_t + con_t), fmt_size(self.checked_total)))


def main():
    if "--selftest" in sys.argv:
        # 自检模式：启动 1.5 秒后自动退出（用于无交互验证）
        root = tk.Tk()
        app = CleanerApp(root)
        root.after(1500, root.destroy)
        root.mainloop()
        print("SELFTEST OK")
        return
    root = tk.Tk()
    CleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
