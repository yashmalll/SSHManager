#!/usr/bin/env python3
"""sshm — TUI-менеджер SSH-соединений (curses, без зависимостей).

Клавиши:
  ↑/↓, j/k   навигация        Enter   подключиться
  /          поиск            a       добавить
  e          редактировать    d       удалить
  c          копировать       i       импорт из ~/.ssh/config
  q / Esc    выход
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


def import_ssh_config(items):
    import re
    path = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(path):
        return 0
    known = {h["name"] for h in items}
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
                       "key": "", "tags": "", "opts": ""}
                found.append(cur)
        elif cur is not None:
            if k == "hostname": cur["host"] = v
            elif k == "user": cur["user"] = v
            elif k == "port": cur["port"] = v
            elif k == "identityfile": cur["key"] = v
    n = 0
    for h in found:
        if h["name"] not in known:
            items.append(h)
            n += 1
    return n


def haystack(h):
    return " ".join(str(h.get(k, "")) for k in ("name", "host", "user", "tags")).lower()


# ── палитра ────────────────────────────────────────────────────────────────
C_TEXT, C_DIM, C_ACC, C_SEL, C_HEAD, C_KEY, C_OK, C_ERR, C_TAG, C_BOX = range(1, 11)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    if curses.COLORS >= 256:
        acc, sel_bg, head_bg, tag, dim = 75, 238, 24, 179, 244
    else:
        acc, sel_bg, head_bg, tag, dim = curses.COLOR_CYAN, curses.COLOR_BLUE, curses.COLOR_BLUE, curses.COLOR_YELLOW, curses.COLOR_WHITE
    P = curses.init_pair
    P(C_TEXT, -1, -1)
    P(C_DIM, dim, -1)
    P(C_ACC, acc, -1)
    P(C_SEL, curses.COLOR_WHITE if curses.COLORS < 256 else 255, sel_bg)
    P(C_HEAD, curses.COLOR_WHITE if curses.COLORS < 256 else 255, head_bg)
    P(C_KEY, curses.COLOR_BLACK, acc)
    P(C_OK, curses.COLOR_GREEN, -1)
    P(C_ERR, curses.COLOR_RED, -1)
    P(C_TAG, tag, -1)
    P(C_BOX, acc, -1)


def cp(n, extra=0):
    return curses.color_pair(n) | extra


class App:
    def __init__(self, scr):
        self.scr = scr
        self.items = load()
        self.query = ""
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
        """Отфильтрованный список; кэшируется до смены запроса или данных."""
        key = (self.query, len(self.items), self.rev)
        if self._vkey != key:
            q = self.query.lower()
            self._view = [h for h in self.items if not q or q in haystack(h)]
            self._vkey = key
        return self._view

    # ── отрисовка ──
    def draw(self):
        s = self.scr
        s.erase()
        H, W = s.getmaxyx()
        if H < 12 or W < 60:
            self.put(0, 0, "Терминал слишком мал (нужно ≥ 60x12)", cp(C_ERR))
            s.refresh()
            return
        v = self.view()
        self.sel = max(0, min(self.sel, len(v) - 1))

        # шапка
        self.put(0, 0, " " * W, cp(C_HEAD))
        self.put(0, 1, "⌁ SSH Manager", cp(C_HEAD, curses.A_BOLD))
        right = f"{len(v)}/{len(self.items)} хостов "
        self.put(0, W - len(right), right, cp(C_HEAD))

        # поиск
        self.box(1, 0, 3, W, "Поиск")
        q = self.query or "нажмите / для поиска…"
        self.put(2, 2, "🔍 " if False else "› ", cp(C_ACC, curses.A_BOLD))
        self.put(2, 4, q, cp(C_TEXT) if self.query else cp(C_DIM))

        # панели
        top_y, ph = 4, H - 6
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
        for i, h in enumerate(v[self.top: self.top + rows]):
            y = top_y + 1 + i
            idx = self.top + i
            name = h["name"]
            host = h["host"]
            text = f" ● {name:<{max(8, lw // 3)}.{max(8, lw // 3)}} {host}"
            text = text[: lw - 3].ljust(lw - 2)
            if idx == self.sel:
                self.put(y, 1, text, cp(C_SEL, curses.A_BOLD))
                self.put(y, 1, " ▌", cp(C_ACC, curses.A_BOLD) | curses.A_REVERSE if False else cp(C_ACC, curses.A_BOLD))
            else:
                self.put(y, 1, text, cp(C_TEXT))
                self.put(y, 3, "●", cp(C_ACC))
        if len(v) > rows:  # скроллбар
            bar_h = max(1, rows * rows // len(v))
            bar_y = (rows - bar_h) * self.top // max(1, len(v) - rows)
            for i in range(bar_h):
                self.put(top_y + 1 + bar_y + i, lw - 1, "┃", cp(C_ACC))

        if v:
            h = v[self.sel]
            x = lw + 3
            self.put(top_y + 1, x, h["name"], cp(C_ACC, curses.A_BOLD))
            self.put(top_y + 2, x, "─" * (rw - 6), cp(C_DIM))
            for i, (k, label) in enumerate(FIELDS[1:]):
                y = top_y + 3 + i
                val = str(h.get(k, "")) or "—"
                self.put(y, x, f"{label:<16}", cp(C_DIM))
                if k == "tags" and h.get(k):
                    cx = x + 16
                    for t in h[k].replace(",", " ").split():
                        self.put(y, cx, f" #{t} ", cp(C_TAG, curses.A_BOLD))
                        cx += len(t) + 3
                else:
                    self.put(y, x + 16, val, cp(C_TEXT))
            y = top_y + 4 + len(FIELDS)
            self.put(y, x, "Команда", cp(C_DIM))
            self.put(y + 1, x, "$ " + " ".join(ssh_cmd(h)), cp(C_OK, curses.A_BOLD))
        else:
            self.put(top_y + 2, 3, "Список пуст", cp(C_DIM, curses.A_BOLD))
            self.put(top_y + 4, 3, "a — добавить хост", cp(C_DIM))
            self.put(top_y + 5, 3, "i — импорт из ~/.ssh/config", cp(C_DIM))

        # статус + подсказки
        text, col = self.msg
        self.put(H - 2, 1, text, cp(col, curses.A_BOLD))
        keys = [("↵", "подкл."), ("a", "новый"), ("e", "ред."), ("c", "копия"), ("d", "удал."),
                ("/", "поиск"), ("i", "импорт"), ("q", "выход")]
        x = 1
        for k, d in keys:
            self.put(H - 1, x, f" {k} ", cp(C_KEY, curses.A_BOLD))
            self.put(H - 1, x + len(k) + 3, f" {d}  ", cp(C_DIM))
            x += len(k) + len(d) + 7
        s.refresh()

    # ── диалоги ──
    def modal(self, h, w, title):
        H, W = self.scr.getmaxyx()
        w = min(w, W - 2)
        y, x = max(0, (H - h) // 2), max(0, (W - w) // 2)
        self.fill(y, x, h, w)
        self.box(y, x, h, w, title)
        return y, x, w

    def confirm(self, text):
        self.draw()
        y, x, w = self.modal(5, max(len(text) + 6, 34), "Подтверждение")
        self.put(y + 2, x + 3, text, cp(C_TEXT, curses.A_BOLD))
        self.put(y + 3, x + 3, "y — да    n/Esc — нет", cp(C_DIM))
        self.scr.refresh()
        while True:
            ch = self.scr.get_wch()
            if ch in ("y", "Y", "н", "Н"):
                return True
            if ch in ("n", "N", "\x1b", "т", "Т"):
                return False

    def read_line(self, y, x, width, buf, pos):
        """Один шаг редактирования поля. Возвращает (action, buf, pos)."""
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
        while True:
            self.draw()
            fh = len(FIELDS) * 2 + 5
            y, x, w = self.modal(fh, 64, title)
            fw = w - 24
            for n, (k, label) in enumerate(FIELDS):
                fy = y + 2 + n * 2
                req = " *" if k in ("name", "host") else ""
                act = n == i
                self.put(fy, x + 3, f"{label + req:<19}", cp(C_ACC, curses.A_BOLD) if act else cp(C_DIM))
                v = vals[k]
                off = max(0, pos - fw + 1) if act else 0
                shown = v[off: off + fw]
                self.put(fy, x + 22, shown.ljust(fw) if act else shown,
                         cp(C_SEL) if act else cp(C_TEXT))
            self.put(y + fh - 2, x + 3, err or "Tab/↑↓ поля · Enter далее · Ctrl+S сохранить · Esc отмена",
                     cp(C_ERR, curses.A_BOLD) if err else cp(C_DIM))
            fw_off = max(0, pos - fw + 1)
            self.scr.move(y + 2 + i * 2, x + 22 + pos - fw_off)
            curses.curs_set(1)
            self.scr.refresh()
            k = FIELDS[i][0]
            act, vals[k], pos = self.read_line(0, 0, fw, vals[k], pos)
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

    def search(self):
        H, W = self.scr.getmaxyx()
        buf, pos = self.query, len(self.query)
        while True:
            self.query = buf
            self.sel = 0
            self.draw()
            self.scr.move(2, 4 + pos)
            curses.curs_set(1)
            self.scr.refresh()
            act, buf, pos = self.read_line(2, 4, W - 6, buf, pos)
            curses.curs_set(0)
            if act == "cancel":
                self.query = ""
                return
            if act in ("next", "save"):
                return

    def connect(self, h):
        curses.endwin()
        os.system("clear")
        cmd = ssh_cmd(h)
        print("\033[1;36m$ " + " ".join(cmd) + "\033[0m\n", flush=True)
        try:
            import subprocess
            subprocess.call(cmd)
        except FileNotFoundError:
            input("ssh не найден. Enter для возврата...")
        self.scr.refresh()

    def run(self):
        while True:
            self.draw()
            try:
                ch = self.scr.get_wch()
            except KeyboardInterrupt:
                return
            self.msg = ("", C_OK)
            v = self.view()
            cur = v[self.sel] if v else None
            if ch in ("q", "й"):
                return
            elif ch == "\x1b":
                if self.query:
                    self.query = ""
                else:
                    return
            elif ch in (curses.KEY_DOWN, "j"):
                self.sel = min(self.sel + 1, len(v) - 1)
            elif ch in (curses.KEY_UP, "k"):
                self.sel = max(self.sel - 1, 0)
            elif ch == curses.KEY_NPAGE:
                self.sel = min(self.sel + 10, len(v) - 1)
            elif ch == curses.KEY_PPAGE:
                self.sel = max(self.sel - 10, 0)
            elif ch in (curses.KEY_HOME, "g"):
                self.sel = 0
            elif ch in (curses.KEY_END, "G"):
                self.sel = max(0, len(v) - 1)
            elif ch in ("\n", "\r", curses.KEY_ENTER) and cur:
                self.connect(cur)
            elif ch == "/":
                self.search()
            elif ch == "a":
                h = self.form({"port": "22"}, "Новый хост")
                if h:
                    self.items.append(h)
                    self.changed()
                    self.msg = (f"Добавлено: {h['name']}", C_OK)
            elif ch == "e" and cur:
                h = self.form(cur, "Редактирование")
                if h:
                    cur.update(h)
                    self.changed()
                    self.msg = ("Сохранено", C_OK)
            elif ch == "c" and cur:
                self.items.append(dict(cur, name=cur["name"] + "-copy"))
                self.changed()
                self.msg = ("Скопировано", C_OK)
            elif ch == "d" and cur:
                if self.confirm(f"Удалить '{cur['name']}'?"):
                    self.items.remove(cur)
                    self.changed()
                    self.msg = ("Удалено", C_ERR)
            elif ch == "i":
                n = import_ssh_config(self.items)
                self.changed()
                self.msg = (f"Импортировано: {n}", C_OK)


def disable_flow_control():
    """Ctrl+S / Ctrl+Q иначе перехватываются терминалом (XON/XOFF)."""
    try:
        import termios
        a = termios.tcgetattr(sys.stdin)
        a[0] &= ~(termios.IXON | termios.IXOFF)
        termios.tcsetattr(sys.stdin, termios.TCSANOW, a)
    except Exception:
        pass


def main():
    if not sys.stdout.isatty():
        sys.exit("Нужен интерактивный терминал")

    def run(scr):
        disable_flow_control()
        App(scr).run()

    curses.wrapper(run)


if __name__ == "__main__":
    main()
