#!/usr/bin/env python3
"""Kon — TUI-менеджер SSH-соединений (curses, без зависимостей).

Внизу — строка ввода: текст фильтрует список, слово-команда + Enter выполняет её.

  ↑/↓ PgUp/PgDn   выбор хоста         Enter   подключиться / выполнить команду
  Ctrl+N          новый хост          Ctrl+E  редактировать
  Ctrl+D          удалить             Ctrl+K  копировать
  Ctrl+O          импорт (с выбором)  Esc     очистить ввод / выход
Команды: a e c d i q (add edit copy del import quit).
"""
import os

os.environ.setdefault("ESCDELAY", "25")  # по умолчанию curses ждёт 1 с после Esc

import curses  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "sshmanager")
DB = os.path.join(CONF_DIR, "hosts.json")
FIELDS = [("name", "Название"), ("host", "Хост"), ("user", "Пользователь"),
          ("port", "Порт"), ("key", "Файл ключа"), ("tags", "Теги"), ("opts", "Доп. опции ssh")]

LOGO = ["█ ▄▀  ▄▀▀▄  █▄ █",
        "██    █  █  █ ▀█",
        "█ ▀▄  ▀▄▄▀  █  █"]
LOGO_SPANS = [(0, 4), (6, 10), (12, 16)]  # границы букв K / o / n

COMMANDS = {"q", "quit", "exit", "й", "a", "add", "new", "e", "edit", "c", "copy",
            "d", "del", "delete", "rm", "i", "import"}


# ── данные ─────────────────────────────────────────────────────────────────
def load():
    try:
        with open(DB, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(items):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = DB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB)


def ssh_cmd(h):
    cmd = ["ssh"]
    if h.get("port") and str(h["port"]) != "22":
        cmd += ["-p", str(h["port"])]
    if h.get("key"):
        cmd += ["-i", os.path.expanduser(h["key"])]
    if h.get("opts"):
        cmd += h["opts"].split()
    cmd.append(f"{h['user']}@{h['host']}" if h.get("user") else h["host"])
    return cmd


def collect_ssh_config(items):
    """Кандидаты из ~/.ssh/config (без шаблонов и уже существующих имён)."""
    import re
    path = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(path):
        return []
    names = {h["name"] for h in items}
    cur, found = None, []
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*(\w+)\s*[= ]\s*(.+?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2)
        if k == "host":
            cur = None
            if not re.search(r"[*?!]", v):
                cur = {"name": v.split()[0], "host": v.split()[0], "user": "", "port": "22",
                       "key": "", "tags": "", "opts": "", "_src": "ssh config"}
                found.append(cur)
        elif cur is not None:
            if k == "hostname": cur["host"] = v
            elif k == "user": cur["user"] = v
            elif k == "port": cur["port"] = v
            elif k == "identityfile": cur["key"] = v
    return [h for h in found if h["name"] not in names]


def collect_known_hosts(items, skip=()):
    """Кандидаты из ~/.ssh/known_hosts (без хешированных, шаблонов и уже существующих)."""
    path = os.path.expanduser("~/.ssh/known_hosts")
    if not os.path.exists(path):
        return []
    seen = {(h["host"], str(h.get("port", "22"))) for h in items}
    seen |= {(h["host"], str(h.get("port", "22"))) for h in skip}
    out = []
    for line in open(path, encoding="utf-8", errors="ignore"):
        parts = line.split()
        if len(parts) < 3 or parts[0].startswith(("#", "|", "@")):
            continue
        entry = parts[0].split(",")[0]
        port = "22"
        if entry.startswith("["):
            host, _, port = entry[1:].partition("]:")
            port = port or "22"
        else:
            host = entry
        if not host or any(c in host for c in "*?!") or (host, port) in seen:
            continue
        seen.add((host, port))
        out.append({"name": host if port == "22" else f"{host}:{port}", "host": host, "user": "",
                    "port": port, "key": "", "tags": "known_hosts", "opts": "", "_src": "known_hosts"})
    return out


def haystack(h):
    return " ".join(str(h.get(k, "")) for k in ("name", "host", "user", "tags")).lower()


def command_word(buf):
    w = buf.strip().lstrip(":/").strip().lower()
    return w if w in COMMANDS else None


# ── палитра ────────────────────────────────────────────────────────────────
(C_TEXT, C_DIM, C_ACC, C_SEL, C_HEAD, C_KEY, C_OK, C_ERR, C_TAG, C_BOX,
 C_L1, C_L2, C_L3) = range(1, 14)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    big = curses.COLORS >= 256
    P = curses.init_pair
    white = 255 if big else curses.COLOR_WHITE
    acc = 75 if big else curses.COLOR_CYAN
    P(C_TEXT, -1, -1)
    P(C_DIM, 244 if big else curses.COLOR_WHITE, -1)
    P(C_ACC, acc, -1)
    P(C_SEL, white, 238 if big else curses.COLOR_BLUE)
    P(C_HEAD, white, 24 if big else curses.COLOR_BLUE)
    P(C_KEY, curses.COLOR_BLACK, acc)
    P(C_OK, curses.COLOR_GREEN, -1)
    P(C_ERR, curses.COLOR_RED, -1)
    P(C_TAG, 179 if big else curses.COLOR_YELLOW, -1)
    P(C_BOX, acc, -1)
    P(C_L1, 141 if big else curses.COLOR_MAGENTA, -1)  # логотип: градиент K → o → n
    P(C_L2, 75 if big else curses.COLOR_CYAN, -1)
    P(C_L3, 78 if big else curses.COLOR_GREEN, -1)


def cp(n, extra=0):
    return curses.color_pair(n) | extra


class App:
    def __init__(self, scr):
        self.scr = scr
        self.items = load()
        self.buf = ""
        self.pos = 0
        self.sel = 0
        self.top = 0
        self.msg = ("", C_OK)
        self.rev = 0
        self._vkey = None
        self._view = []
        curses.curs_set(0)
        init_colors()
        scr.keypad(True)

    # ── примитивы ──
    def put(self, y, x, s, attr=0):
        H, W = self.scr.getmaxyx()
        if 0 <= y < H and 0 <= x < W:
            try:
                self.scr.addstr(y, x, s[: max(0, W - x - (1 if y == H - 1 else 0))], attr)
            except curses.error:
                pass

    def box(self, y, x, h, w, title="", attr=None):
        attr = cp(C_BOX) if attr is None else attr
        if h < 2 or w < 2:
            return
        self.put(y, x, "╭" + "─" * (w - 2) + "╮", attr)
        for i in range(1, h - 1):
            self.put(y + i, x, "│", attr)
            self.put(y + i, x + w - 1, "│", attr)
        self.put(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", attr)
        if title:
            self.put(y, x + 2, f" {title} ", attr | curses.A_BOLD)

    def fill(self, y, x, h, w):
        for i in range(h):
            self.put(y + i, x, " " * w)

    def changed(self):
        self.rev += 1
        save(self.items)

    def view(self):
        """Отфильтрованный список. Слово-команда в строке ввода список не фильтрует."""
        q = "" if command_word(self.buf) else self.buf.lower()
        key = (q, len(self.items), self.rev)
        if self._vkey != key:
            self._view = [h for h in self.items if not q or q in haystack(h)]
            self._vkey = key
        return self._view

    def current(self):
        v = self.view()
        return v[self.sel] if v and 0 <= self.sel < len(v) else None

    def touch(self):
        """После правки строки ввода: при смене фильтра выбор возвращается на первый хост."""
        if not command_word(self.buf):
            self.sel = 0

    # ── отрисовка ──
    def draw(self, focus=False):
        s = self.scr
        s.erase()
        H, W = s.getmaxyx()
        if H < 12 or W < 60:
            self.put(0, 0, "Терминал слишком мал (нужно ≥ 60x12)", cp(C_ERR))
            s.refresh()
            return
        v = self.view()
        self.sel = max(0, min(self.sel, len(v) - 1))

        # шапка с логотипом
        big = H >= 24
        if big:
            for r, line in enumerate(LOGO):
                for (a, b), col in zip(LOGO_SPANS, (C_L1, C_L2, C_L3)):
                    self.put(r, 2 + a, line[a:b], cp(col, curses.A_BOLD))
            self.put(0, 22, "SSH connection manager", cp(C_TEXT, curses.A_BOLD))
            self.put(1, 22, "быстрый доступ к вашим серверам", cp(C_DIM))
            stat = f"{len(v)}/{len(self.items)} хостов"
            self.put(0, W - len(stat) - 2, stat, cp(C_ACC, curses.A_BOLD))
            top_y = 3
        else:
            self.put(0, 0, " " * W, cp(C_HEAD))
            self.put(0, 2, "K", cp(C_L1, curses.A_BOLD | curses.A_REVERSE))
            self.put(0, 3, "o", cp(C_L2, curses.A_BOLD | curses.A_REVERSE))
            self.put(0, 4, "n", cp(C_L3, curses.A_BOLD | curses.A_REVERSE))
            self.put(0, 7, "SSH manager", cp(C_HEAD, curses.A_BOLD))
            stat = f"{len(v)}/{len(self.items)} "
            self.put(0, W - len(stat), stat, cp(C_HEAD))
            top_y = 1
        ph = H - 4 - top_y
        lw = max(32, W * 2 // 5)
        rw = W - lw
        self.box(top_y, 0, ph, lw, "Хосты")
        self.box(top_y, lw, ph, rw, "Детали")

        rows = ph - 2
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        self.top = max(0, min(self.top, max(0, len(v) - rows)))
        nw = max(8, lw // 3)
        for i, h in enumerate(v[self.top: self.top + rows]):
            y = top_y + 1 + i
            text = f" ● {h['name']:<{nw}.{nw}} {h['host']}"[: lw - 3].ljust(lw - 2)
            if self.top + i == self.sel:
                self.put(y, 1, text, cp(C_SEL, curses.A_BOLD))
                self.put(y, 1, "▌", cp(C_ACC, curses.A_BOLD))
            else:
                self.put(y, 1, text, cp(C_TEXT))
                self.put(y, 3, "●", cp(C_ACC))
        if len(v) > rows:  # скроллбар
            bar_h = max(1, rows * rows // len(v))
            bar_y = (rows - bar_h) * self.top // max(1, len(v) - rows)
            for i in range(bar_h):
                self.put(top_y + 1 + bar_y + i, lw - 1, "┃", cp(C_ACC))

        cur = self.current()
        if cur:
            x = lw + 3
            self.put(top_y + 1, x, cur["name"], cp(C_ACC, curses.A_BOLD))
            self.put(top_y + 2, x, "─" * max(0, rw - 6), cp(C_DIM))
            for i, (k, label) in enumerate(FIELDS[1:]):
                y = top_y + 3 + i
                self.put(y, x, f"{label:<16}", cp(C_DIM))
                if k == "tags" and cur.get(k):
                    cx = x + 16
                    for t in cur[k].replace(",", " ").split():
                        self.put(y, cx, f" #{t} ", cp(C_TAG, curses.A_BOLD))
                        cx += len(t) + 3
                else:
                    self.put(y, x + 16, str(cur.get(k, "")) or "—", cp(C_TEXT))
            y = top_y + 4 + len(FIELDS)
            self.put(y, x, "Команда", cp(C_DIM))
            self.put(y + 1, x, "$ " + " ".join(ssh_cmd(cur)), cp(C_OK, curses.A_BOLD))
        else:
            self.put(top_y + 2, 3, "Список пуст", cp(C_DIM, curses.A_BOLD))
            self.put(top_y + 4, 3, "a — добавить хост", cp(C_DIM))
            self.put(top_y + 5, 3, "i — импорт с выбором", cp(C_DIM))

        # строка ввода (поиск + команды)
        self.box(H - 4, 0, 3, W, "Поиск / команда")
        self.put(H - 3, 2, "›", cp(C_ACC, curses.A_BOLD))
        if self.buf:
            self.put(H - 3, 4, self.buf, cp(C_ACC if command_word(self.buf) else C_TEXT))
        else:
            self.put(H - 3, 4, "введите текст для поиска или команду (a e c d i q)…", cp(C_DIM))
        text, col = self.msg
        if text:
            self.put(H - 3, max(4, W - len(text) - 3), text, cp(col, curses.A_BOLD))
        keys = [("↑↓", "выбор"), ("↵", "подкл."), ("^N", "новый"), ("^E", "ред."), ("^D", "удал."),
                ("^K", "копия"), ("^O", "импорт"), ("q", "выход")]
        x = 1
        for k, d in keys:
            self.put(H - 1, x, f" {k} ", cp(C_KEY, curses.A_BOLD))
            self.put(H - 1, x + len(k) + 2, f" {d} ", cp(C_DIM))
            x += len(k) + len(d) + 6
        if focus:
            s.move(H - 3, 4 + self.pos)
        s.refresh()

    # ── диалоги ──
    def modal(self, h, w, title):
        H, W = self.scr.getmaxyx()
        w = min(w, W - 2)
        h = min(h, H)
        y, x = max(0, (H - h) // 2), max(0, (W - w) // 2)
        self.fill(y, x, h, w)
        self.box(y, x, h, w, title)
        return y, x, w

    def confirm(self, text):
        curses.curs_set(0)
        self.draw()
        y, x, w = self.modal(5, max(len(text) + 6, 36), "Подтверждение")
        self.put(y + 2, x + 3, text, cp(C_TEXT, curses.A_BOLD))
        self.put(y + 3, x + 3, "y/Enter — да    n/Esc — нет", cp(C_DIM))
        self.scr.refresh()
        while True:
            ch = self.scr.get_wch()
            if ch in ("y", "Y", "н", "Н", "\n", "\r", curses.KEY_ENTER):
                return True
            if ch in ("n", "N", "\x1b", "т", "Т"):
                return False

    def read_line(self, buf, pos):
        """Один шаг редактирования поля формы. Возвращает (action, buf, pos)."""
        ch = self.scr.get_wch()
        if ch in ("\n", "\r") or ch == curses.KEY_ENTER:
            return "next", buf, pos
        if ch == "\x1b":
            return "cancel", buf, pos
        if ch in ("\t", curses.KEY_DOWN):
            return "next", buf, pos
        if ch in (curses.KEY_BTAB, curses.KEY_UP):
            return "prev", buf, pos
        if ch == curses.KEY_LEFT:
            pos = max(0, pos - 1)
        elif ch == curses.KEY_RIGHT:
            pos = min(len(buf), pos + 1)
        elif ch in (curses.KEY_HOME, "\x01"):
            pos = 0
        elif ch in (curses.KEY_END, "\x05"):
            pos = len(buf)
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if pos:
                buf = buf[: pos - 1] + buf[pos:]
                pos -= 1
        elif ch == curses.KEY_DC:
            buf = buf[:pos] + buf[pos + 1:]
        elif ch == "\x15":
            buf, pos = "", 0
        elif ch == "\x13":
            return "save", buf, pos
        elif isinstance(ch, str) and ch.isprintable():
            buf = buf[:pos] + ch + buf[pos:]
            pos += 1
        return "stay", buf, pos

    def form(self, h, title):
        vals = {k: str(h.get(k, "")) for k, _ in FIELDS}
        i, pos = 0, len(vals["name"])
        err = ""
        H, _ = self.scr.getmaxyx()
        step = 2 if H >= len(FIELDS) * 2 + 6 else 1
        while True:
            self.draw()
            fh = len(FIELDS) * step + 4
            y, x, w = self.modal(fh, 64, title)
            fw = max(8, w - 24)
            for n, (k, label) in enumerate(FIELDS):
                fy = y + 1 + n * step + (1 if step == 2 else 0)
                req = " *" if k in ("name", "host") else ""
                act = n == i
                self.put(fy, x + 3, f"{label + req:<19}", cp(C_ACC, curses.A_BOLD) if act else cp(C_DIM))
                v = vals[k]
                off = max(0, pos - fw + 1) if act else 0
                shown = v[off: off + fw]
                self.put(fy, x + 22, shown.ljust(fw) if act else shown, cp(C_SEL) if act else cp(C_TEXT))
            self.put(y + fh - 2, x + 3, err or "Tab/↑↓ поля · Enter далее · Ctrl+S сохранить · Esc отмена",
                     cp(C_ERR, curses.A_BOLD) if err else cp(C_DIM))
            off = max(0, pos - fw + 1)
            self.scr.move(y + 1 + i * step + (1 if step == 2 else 0), x + 22 + pos - off)
            curses.curs_set(1)
            self.scr.refresh()
            k = FIELDS[i][0]
            act, vals[k], pos = self.read_line(vals[k], pos)
            curses.curs_set(0)
            if act == "cancel":
                return None
            if act in ("next", "prev", "save"):
                if act == "next" and i < len(FIELDS) - 1:
                    i += 1
                elif act == "prev" and i > 0:
                    i -= 1
                elif act == "save" or (act == "next" and i == len(FIELDS) - 1):
                    if not vals["name"].strip() or not vals["host"].strip():
                        err = "Название и хост обязательны"
                        i = 0 if not vals["name"].strip() else 1
                    else:
                        out = dict(h)
                        out.update({k2: v2.strip() for k2, v2 in vals.items()})
                        out["port"] = out.get("port") or "22"
                        return out
                pos = len(vals[FIELDS[i][0]])
                if act != "save":
                    err = ""

    def picker(self, cands, title):
        """Список с флажками. Space — отметить, a — все, n — снять все, Enter — готово."""
        checked = set()
        cur = top = 0
        curses.curs_set(0)
        while True:
            self.draw()
            H, W = self.scr.getmaxyx()
            y, x, w = self.modal(min(H - 2, len(cands) + 6), min(W - 4, 84), title)
            rows = max(1, min(H - 2, len(cands) + 6) - 6)
            if cur < top:
                top = cur
            if cur >= top + rows:
                top = cur - rows + 1
            self.put(y + 1, x + 2, f"Выбрано: {len(checked)} из {len(cands)}", cp(C_ACC, curses.A_BOLD))
            for r, h in enumerate(cands[top: top + rows]):
                idx = top + r
                mark = "[x]" if idx in checked else "[ ]"
                addr = h["host"] + ("" if str(h["port"]) == "22" else f":{h['port']}")
                who = f"{h['user']}@" if h["user"] else ""
                line = f" {mark} {h['name']:<22.22} {who + addr:<30.30} {h['_src']}"[: w - 4].ljust(w - 4)
                attr = cp(C_SEL, curses.A_BOLD) if idx == cur else cp(C_OK if idx in checked else C_TEXT)
                self.put(y + 2 + r, x + 2, line, attr)
            self.put(y + rows + 3, x + 2,
                     "↑↓ выбор · Space отметить · a все · n снять · Enter импорт · Esc отмена",
                     cp(C_DIM))
            self.scr.refresh()
            ch = self.scr.get_wch()
            if ch == curses.KEY_DOWN:
                cur = min(cur + 1, len(cands) - 1)
            elif ch == curses.KEY_UP:
                cur = max(cur - 1, 0)
            elif ch == curses.KEY_NPAGE:
                cur = min(cur + rows, len(cands) - 1)
            elif ch == curses.KEY_PPAGE:
                cur = max(cur - rows, 0)
            elif ch == " ":
                checked ^= {cur}
                cur = min(cur + 1, len(cands) - 1)
            elif ch in ("a", "ф"):
                checked = set(range(len(cands)))
            elif ch in ("n", "т"):
                checked = set()
            elif ch in ("\n", "\r", curses.KEY_ENTER):
                return [cands[i] for i in sorted(checked)]
            elif ch == "\x1b":
                return None

    def unique_name(self, name):
        names = {h["name"] for h in self.items}
        while name in names:
            name += "_"
        return name

    def do_import(self):
        a = collect_ssh_config(self.items)
        b = collect_known_hosts(self.items, skip=a)
        cands = a + b
        if not cands:
            self.msg = ("Нечего импортировать (~/.ssh/config и known_hosts: новых хостов нет)", C_ERR)
            return
        chosen = self.picker(cands, "Импорт хостов")
        if chosen is None:
            self.msg = ("Импорт отменён", C_DIM)
        elif not chosen:
            self.msg = ("Ничего не выбрано", C_DIM)
        else:
            for h in chosen:
                h = {k: v for k, v in h.items() if k != "_src"}
                h["name"] = self.unique_name(h["name"])
                self.items.append(h)
            self.changed()
            self.msg = (f"Импортировано: {len(chosen)}", C_OK)

    def connect(self, h):
        curses.endwin()
        os.system("clear")
        print("\033[1;36m$ " + " ".join(ssh_cmd(h)) + "\033[0m\n", flush=True)
        try:
            import subprocess
            subprocess.call(ssh_cmd(h))
        except FileNotFoundError:
            input("ssh не найден. Enter для возврата...")
        self.scr.refresh()

    # ── действия ──
    def act_add(self):
        h = self.form({"port": "22"}, "Новый хост")
        if h:
            self.items.append(h)
            self.changed()
            self.msg = (f"Добавлено: {h['name']}", C_OK)

    def act_edit(self, cur):
        if not cur:
            self.msg = ("Нет выбранного хоста", C_ERR)
            return
        h = self.form(cur, f"Редактирование: {cur['name']}")
        if h:
            cur.update(h)
            self.changed()
            self.msg = ("Сохранено", C_OK)

    def act_copy(self, cur):
        if not cur:
            self.msg = ("Нет выбранного хоста", C_ERR)
            return
        self.items.append(dict(cur, name=self.unique_name(cur["name"] + "-copy")))
        self.changed()
        self.msg = ("Скопировано", C_OK)

    def act_delete(self, cur):
        if not cur:
            self.msg = ("Нет выбранного хоста", C_ERR)
        elif self.confirm(f"Удалить '{cur['name']}'?"):
            self.items.remove(cur)
            self.changed()
            self.msg = (f"Удалено: {cur['name']}", C_ERR)

    def command(self, name, cur):
        """Выполняет команду. Возвращает True, если нужно выйти."""
        if name in ("q", "quit", "exit", "й"):
            return True
        if name in ("a", "add", "new"):
            self.act_add()
        elif name in ("e", "edit"):
            self.act_edit(cur)
        elif name in ("c", "copy"):
            self.act_copy(cur)
        elif name in ("d", "del", "delete", "rm"):
            self.act_delete(cur)
        elif name in ("i", "import"):
            self.do_import()
        return False

    def run(self):
        while True:
            self.draw(focus=True)
            curses.curs_set(1)
            try:
                ch = self.scr.get_wch()
            except KeyboardInterrupt:
                return
            self.msg = ("", C_OK)
            v = self.view()
            cur = self.current()
            if ch == "\x03":  # Ctrl+C (терминал в raw-режиме)
                return
            elif ch == curses.KEY_DOWN:
                self.sel = min(self.sel + 1, len(v) - 1)
            elif ch == curses.KEY_UP:
                self.sel = max(self.sel - 1, 0)
            elif ch == curses.KEY_NPAGE:
                self.sel = min(self.sel + 10, len(v) - 1)
            elif ch == curses.KEY_PPAGE:
                self.sel = max(self.sel - 10, 0)
            elif ch == "\x0e":
                curses.curs_set(0); self.act_add()
            elif ch == "\x05":
                curses.curs_set(0); self.act_edit(cur)
            elif ch == "\x04":
                curses.curs_set(0); self.act_delete(cur)
            elif ch == "\x0b":
                self.act_copy(cur)
            elif ch == "\x0f":
                curses.curs_set(0); self.do_import()
            elif ch in ("\n", "\r", curses.KEY_ENTER):
                word = command_word(self.buf)
                if word:
                    self.buf, self.pos = "", 0  # хост остаётся выбранным (список был не отфильтрован)
                    curses.curs_set(0)
                    if self.command(word, cur):
                        return
                elif cur:
                    self.connect(cur)
            elif ch == "\x1b":
                if self.buf:
                    self.buf, self.pos, self.sel = "", 0, 0
                else:
                    return
            elif ch == curses.KEY_LEFT:
                self.pos = max(0, self.pos - 1)
            elif ch == curses.KEY_RIGHT:
                self.pos = min(len(self.buf), self.pos + 1)
            elif ch in (curses.KEY_HOME, "\x01"):
                self.pos = 0
            elif ch == curses.KEY_END:
                self.pos = len(self.buf)
            elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if self.pos:
                    self.buf = self.buf[: self.pos - 1] + self.buf[self.pos:]
                    self.pos -= 1
                    self.touch()
            elif ch == curses.KEY_DC:
                if self.buf:
                    self.buf = self.buf[: self.pos] + self.buf[self.pos + 1:]
                    self.touch()
                else:
                    curses.curs_set(0); self.act_delete(cur)
            elif ch == "\x15":
                self.buf, self.pos, self.sel = "", 0, 0
            elif isinstance(ch, str) and ch.isprintable():
                self.buf = self.buf[: self.pos] + ch + self.buf[self.pos:]
                self.pos += 1
                self.touch()


def main():
    if not sys.stdout.isatty():
        sys.exit("Нужен интерактивный терминал")

    def run(scr):
        curses.raw()  # Ctrl+S/Ctrl+Q/Ctrl+O/Ctrl+C приходят приложению как обычные клавиши
        App(scr).run()

    curses.wrapper(run)


if __name__ == "__main__":
    main()
