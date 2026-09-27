#!/usr/bin/env python3
"""Python Workbench: project, pip, requirements, code checks, and shebangs.

Standard library GUI; optional formatting/build tools run in the selected Python.
"""
from __future__ import annotations

import ast
import os
import queue
import re
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

EXCLUDES = {".git", "__pycache__", ".venv", "venv", "build", "dist", ".tox", "node_modules"}
TOOLS = ("black", "ruff", "flake8", "pylint", "isort", "autopep8")
REQ = re.compile(r"^(\s*)([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._, -]+\])?)\s*(?:===|==|~=|!=|<=|>=|<|>)(.*)$")


def unpin_requirements(content: str) -> tuple[str, int]:
    """Remove specifiers from plain package lines; retain comments and complex syntax."""
    result, count = [], 0
    for line in content.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.strip()
        if not stripped or stripped.startswith(("#", "-")) or " @ " in body or "git+" in body or "${" in body:
            result.append(line)
            continue
        match = REQ.match(body)
        if match:
            name = match.group(1) + match.group(2)
            marker = re.search(r"\s*;\s*[^#]+", body)
            comment = re.search(r"\s+#.*$", body)
            new = name + (marker.group() if marker else "") + (comment.group() if comment else "")
            result.append(new + ending)
            count += new != body
        else:
            result.append(line)
    return "".join(result), count


class Workbench(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Python Workbench")
        self.geometry("1100x780")
        self.minsize(850, 600)
        self.style = ttk.Style(self)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self.style.configure("TButton", padding=(10, 5))
        self.style.configure("TNotebook.Tab", padding=(13, 7))
        self.project = tk.StringVar(value=str(Path.cwd()))
        self.python = tk.StringVar(value=sys.executable)
        self.script = tk.StringVar()
        self.venv = tk.StringVar(value=".venv")
        self.reqpath = tk.StringVar(value=str(Path.cwd() / "requirements.txt"))
        self.package = tk.StringVar()
        self.break_system = tk.BooleanVar()
        self.user_install = tk.BooleanVar()
        self.recursive = tk.BooleanVar(value=True)
        self.backup = tk.BooleanVar(value=True)
        self.shebang = tk.StringVar(value="#!/usr/bin/env python3")
        self.copy_shebang = tk.BooleanVar(value=True)
        self.shebang_out = tk.StringVar()
        self.make_executable = tk.BooleanVar()
        self.blank_line = tk.BooleanVar(value=True)
        self.only_missing = tk.BooleanVar()
        self.selected = {name: tk.BooleanVar(value=name in ("black", "ruff")) for name in TOOLS}
        self.onefile = tk.BooleanVar(value=True)
        self.events = queue.Queue()
        self.process = None
        self.busy = False
        self.files = []
        self._ui()
        self.after(100, self._poll)

    def row(self, parent, number, label, variable, chooser=None):
        ttk.Label(parent, text=label).grid(row=number, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=number, column=1, sticky="ew", padx=6, pady=6)
        if chooser:
            ttk.Button(parent, text="Browse", command=chooser).grid(row=number, column=2, padx=6)

    def buttonrow(self, parent, labels):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=8)
        for label, action in labels:
            ttk.Button(bar, text=label, command=action).pack(side="left", padx=4)

    def _ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        target = ttk.LabelFrame(self, text="Project and Python interpreter")
        target.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        target.columnconfigure(1, weight=1)
        self.row(target, 0, "Project folder", self.project, lambda: self.choose_dir(self.project))
        self.row(target, 1, "Python executable", self.python, lambda: self.choose_file(self.python))
        ttk.Button(target, text="Use project .venv", command=self.use_venv).grid(row=1, column=3, padx=5)
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10)
        self._project_tab()
        self._packages_tab()
        self._requirements_tab()
        self._format_tab()
        self._shebang_tab()
        self._build_tab()
        bottom = ttk.LabelFrame(self, text="Command output")
        bottom.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)
        bar = ttk.Frame(bottom)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Stop command", command=self.stop).pack(side="left")
        ttk.Button(bar, text="Clear output", command=lambda: self.output.delete("1.0", "end")).pack(side="left")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status).pack(side="right", padx=8)
        self.output = ScrolledText(bottom, height=11, wrap="word")
        self.output.grid(row=1, column=0, sticky="nsew")

    def tab(self, title):
        pane = ttk.Frame(self.tabs, padding=10)
        pane.columnconfigure(1, weight=1)
        self.tabs.add(pane, text=title)
        return pane

    def _project_tab(self):
        f = self.tab("Project")
        self.row(f, 0, "Main script", self.script, lambda: self.choose_file(self.script))
        self.row(f, 1, "Virtual environment", self.venv)
        self.buttonrow_grid(f, 2, [("Create project folder", self.create_project), ("Create environment", self.create_venv),
                                   ("Run script", self.run_script), ("Python / pip versions", self.versions),
                                   ("Activation command", self.activation)])

    def buttonrow_grid(self, f, row, actions):
        bar = ttk.Frame(f)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=10)
        for label, action in actions:
            ttk.Button(bar, text=label, command=action).pack(side="left", padx=3)

    def _packages_tab(self):
        f = self.tab("Packages")
        self.row(f, 0, "Package / pip arguments", self.package)
        ttk.Checkbutton(f, text="--break-system-packages (Debian/MX; use with care)", variable=self.break_system).grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(f, text="--user", variable=self.user_install).grid(row=2, column=0, sticky="w")
        self.buttonrow_grid(f, 3, [("Install", lambda: self.package_action("install")), ("Upgrade", lambda: self.package_action("install", "--upgrade")),
                                   ("Uninstall", lambda: self.package_action("uninstall")), ("Show", lambda: self.package_action("show"))])
        self.buttonrow_grid(f, 4, [("List installed", lambda: self.pip("list")), ("List outdated", lambda: self.pip("list", "--outdated")),
                                   ("Pip check", lambda: self.pip("check")), ("Upgrade pip", lambda: self.pip("install", "--upgrade", "pip"))])

    def _requirements_tab(self):
        f = self.tab("Requirements")
        f.rowconfigure(2, weight=1)
        self.row(f, 0, "Requirements file", self.reqpath, lambda: self.choose_file(self.reqpath))
        self.buttonrow_grid(f, 1, [("Load", self.load_req), ("Freeze target", self.freeze), ("Remove versions in editor", self.unpin),
                                   ("Save as…", self.save_req), ("Install saved file", self.install_req)])
        self.reqtext = ScrolledText(f, wrap="none", undo=True, font=("TkFixedFont", 10))
        self.reqtext.grid(row=2, column=0, columnspan=3, sticky="nsew")
        ttk.Label(f, text="Comments and pip directives are preserved. Direct URLs are left unchanged; review changes before saving.").grid(row=3, column=0, columnspan=3, sticky="w")

    def _format_tab(self):
        f = self.tab("Format and lint")
        f.rowconfigure(3, weight=1)
        ttk.Checkbutton(f, text="Search subfolders", variable=self.recursive).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(f, text="Back up files before formatting", variable=self.backup).grid(row=0, column=1, sticky="w")
        options = ttk.Frame(f)
        options.grid(row=1, column=0, columnspan=3, sticky="w")
        for name in TOOLS:
            ttk.Checkbutton(options, text=name, variable=self.selected[name]).pack(side="left", padx=8)
        self.buttonrow_grid(f, 2, [("Scan", self.scan), ("Check / report", lambda: self.format_action(False)),
                                   ("Format / fix", lambda: self.format_action(True)), ("Tool status", self.tool_status)])
        self.filebox = tk.Listbox(f)
        self.filebox.grid(row=3, column=0, columnspan=3, sticky="nsew")

    def _shebang_tab(self):
        f = self.tab("Shebangs")
        self.row(f, 0, "Shebang", self.shebang)
        self.row(f, 1, "Copy output folder", self.shebang_out, lambda: self.choose_dir(self.shebang_out))
        for row, label, var in [(2, "Copy files to output folder (recommended)", self.copy_shebang),
                                (3, "Blank line after shebang", self.blank_line), (4, "Only add missing shebangs", self.only_missing),
                                (5, "Make executable on Linux/macOS", self.make_executable)]:
            ttk.Checkbutton(f, text=label, variable=var).grid(row=row, column=0, columnspan=3, sticky="w", pady=3)
        self.buttonrow_grid(f, 6, [("Preview", self.shebang_preview), ("Apply", self.shebang_apply)])

    def _build_tab(self):
        f = self.tab("Build")
        ttk.Checkbutton(f, text="PyInstaller one-file mode", variable=self.onefile).grid(row=0, column=0, sticky="w")
        self.buttonrow_grid(f, 1, [("Compile bytecode", lambda: self.run([self.python_path(), "-m", "compileall", self.project.get()])),
                                   ("PyInstaller", lambda: self.build("PyInstaller", ["--onefile"] if self.onefile.get() else [])),
                                   ("Nuitka", lambda: self.build("nuitka", ["--onefile"] if self.onefile.get() else [])),
                                   ("cx_Freeze", lambda: self.build("cx_Freeze", []))])
        ttk.Label(f, text="Optional build tools must be installed in the selected interpreter. cx_Freeze requires a setup.py in the project.").grid(row=2, column=0, columnspan=3, sticky="w")

    def choose_dir(self, variable):
        value = filedialog.askdirectory(initialdir=self.project.get() if Path(self.project.get()).is_dir() else None)
        if value: variable.set(value)

    def choose_file(self, variable):
        value = filedialog.askopenfilename(initialdir=self.project.get() if Path(self.project.get()).is_dir() else None)
        if value: variable.set(value)

    def project_path(self):
        path = Path(self.project.get()).expanduser()
        if not path.is_dir(): raise ValueError("Select an existing project folder")
        return path

    def python_path(self):
        path = Path(self.python.get()).expanduser()
        if not path.is_file(): raise ValueError("Select a Python executable")
        return str(path)

    def use_venv(self):
        path = Path(self.project.get()) / self.venv.get() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if path.is_file(): self.python.set(str(path))
        else: messagebox.showinfo("Environment", f"Python not found at {path}")

    def log(self, message):
        self.output.insert("end", message + "\n")
        self.output.see("end")

    def run(self, argv, callback=None):
        try:
            cwd = self.project_path()
            if self.busy: raise ValueError("Another command is running; stop it first")
        except ValueError as exc:
            messagebox.showwarning("Command", str(exc)); return
        self.busy = True
        self.status.set("Running")
        self.log("$ " + shlex.join([str(x) for x in argv]))
        def worker():
            try:
                self.process = subprocess.Popen([str(x) for x in argv], cwd=cwd, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
                chunks = []
                for line in self.process.stdout:
                    chunks.append(line)
                    self.events.put(("line", line))
                code = self.process.wait()
                self.events.put(("done", (code, "".join(chunks), callback)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line": self.log(payload.rstrip("\n"))
                elif kind == "done":
                    code, output, callback = payload
                    self.busy = False; self.process = None
                    self.status.set(f"Finished (exit {code})")
                    if callback: callback(code, output)
                elif kind == "error":
                    self.busy = False; self.process = None
                    self.status.set("Error"); self.log(payload)
        except queue.Empty: pass
        self.after(100, self._poll)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status.set("Stopping")

    def pip(self, *args):
        try: self.run([self.python_path(), "-m", "pip", *args])
        except ValueError as exc: messagebox.showwarning("Python", str(exc))

    def package_action(self, action, *extra):
        try:
            args = shlex.split(self.package.get())
            if not args: raise ValueError("Enter a package name")
            flags = (["--break-system-packages"] if self.break_system.get() and action in ("install", "uninstall") else [])
            if self.user_install.get() and action == "install": flags.append("--user")
            self.pip(action, *extra, *args, *flags, *(["-y"] if action == "uninstall" else []))
        except ValueError as exc: messagebox.showwarning("Packages", str(exc))

    def create_project(self):
        path = Path(self.project.get()).expanduser()
        if path.exists(): return messagebox.showinfo("Project", "Folder already exists")
        if messagebox.askyesno("Create project", f"Create {path}?"):
            try: path.mkdir(parents=True); self.log(f"Created {path}")
            except OSError as exc: messagebox.showerror("Project", str(exc))

    def create_venv(self):
        try:
            name = self.venv.get().strip()
            if not name or Path(name).is_absolute() or ".." in Path(name).parts: raise ValueError("Use a relative environment folder name")
            self.run([self.python_path(), "-m", "venv", str(self.project_path() / name)])
        except ValueError as exc: messagebox.showwarning("Environment", str(exc))

    def script_path(self):
        path = Path(self.script.get()).expanduser()
        if not path.is_file(): raise ValueError("Select a Python script first")
        return str(path)

    def run_script(self):
        try: self.run([self.python_path(), self.script_path()])
        except ValueError as exc: messagebox.showwarning("Script", str(exc))

    def versions(self):
        try: self.run([self.python_path(), "-m", "pip", "--version"])
        except ValueError as exc: messagebox.showwarning("Python", str(exc))

    def activation(self):
        path = self.project_path() / self.venv.get() / ("Scripts/activate" if os.name == "nt" else "bin/activate")
        self.log(f"Activate in a terminal: {'call' if os.name == 'nt' else 'source'} {shlex.quote(str(path))}")

    def load_req(self):
        path = Path(self.reqpath.get()).expanduser()
        try:
            data = path.read_text(encoding="utf-8-sig")
            self.reqtext.delete("1.0", "end"); self.reqtext.insert("1.0", data)
            self.log(f"Loaded {path}")
        except OSError as exc: messagebox.showerror("Requirements", str(exc))

    def freeze(self):
        try:
            python = self.python_path()
            self.run([python, "-m", "pip", "freeze"], lambda code, data: self._frozen(code, data))
        except ValueError as exc: messagebox.showwarning("Requirements", str(exc))

    def _frozen(self, code, data):
        if code == 0:
            self.reqtext.delete("1.0", "end"); self.reqtext.insert("1.0", data)
            self.tabs.select(2)

    def unpin(self):
        data = self.reqtext.get("1.0", "end-1c")
        changed, count = unpin_requirements(data)
        self.reqtext.delete("1.0", "end"); self.reqtext.insert("1.0", changed)
        self.log(f"Removed version constraints from {count} requirement(s); review and Save as…")

    def save_req(self):
        path = filedialog.asksaveasfilename(initialdir=self.project.get(), initialfile=Path(self.reqpath.get()).name,
                                            defaultextension=".txt", filetypes=[("Requirements", "*.txt"), ("All files", "*")])
        if not path: return
        target = Path(path)
        if target.exists() and not messagebox.askyesno("Overwrite", f"Replace {target}?"): return
        try:
            target.write_text(self.reqtext.get("1.0", "end-1c"), encoding="utf-8")
            self.reqpath.set(str(target)); self.log(f"Saved {target}")
        except OSError as exc: messagebox.showerror("Requirements", str(exc))

    def install_req(self):
        path = Path(self.reqpath.get()).expanduser()
        if not path.is_file(): return messagebox.showwarning("Requirements", "Save the requirements file first")
        flags = ["--break-system-packages"] if self.break_system.get() else []
        if self.user_install.get(): flags.append("--user")
        self.pip("install", "-r", str(path), *flags)

    def scan(self):
        try:
            root = self.project_path()
            files = root.rglob("*.py") if self.recursive.get() else root.glob("*.py")
            self.files = sorted(p for p in files if not any(part in EXCLUDES for part in p.relative_to(root).parts))
            self.filebox.delete(0, "end")
            for path in self.files: self.filebox.insert("end", str(path.relative_to(root)))
            self.log(f"Found {len(self.files)} Python files")
        except ValueError as exc: messagebox.showwarning("Scan", str(exc))

    def tool_status(self):
        try:
            for tool in TOOLS:
                proc = subprocess.run([self.python_path(), "-c", f"import importlib.util; print(bool(importlib.util.find_spec('{tool}')))"] ,
                                      capture_output=True, text=True, timeout=5)
                self.log(f"{tool}: {'available' if proc.stdout.strip() == 'True' else 'not installed'}")
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc: messagebox.showerror("Tools", str(exc))

    def format_action(self, fix):
        if not self.files: self.scan()
        if not self.files: return
        chosen = [tool for tool in TOOLS if self.selected[tool].get()]
        if fix and not chosen: return messagebox.showwarning("Format", "Select a formatter")
        if fix and not messagebox.askyesno("Format files", f"Run selected tools on {len(self.files)} files?"): return
        try:
            python = self.python_path()
            for path in self.files:
                try:
                    ast.parse(path.read_bytes(), filename=str(path))
                except (SyntaxError, OSError, UnicodeError) as exc: self.log(f"Syntax: {path}: {exc}")
            if fix and self.backup.get():
                for path in self.files:
                    shutil.copy2(path, path.with_name(path.name + ".bak"))
            commands = []
            for tool in chosen:
                args = {"black": [] if fix else ["--check"], "ruff": ["check"] + (["--fix"] if fix else []),
                        "flake8": [], "pylint": [], "isort": [] if fix else ["--check-only"],
                        "autopep8": ["--in-place"] if fix else ["--diff"]}[tool]
                commands.append([python, "-m", tool, *args, *map(str, self.files)])
            def next_command(code=0, output=""):
                if commands:
                    self.run(commands.pop(0), next_command)
            next_command()
        except (ValueError, OSError) as exc: messagebox.showerror("Format", str(exc))

    def _new_shebang(self, data):
        text = data.decode("utf-8-sig", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        old = bool(lines and lines[0].startswith("#!"))
        if old and self.only_missing.get(): return data
        newline = "\r\n" if b"\r\n" in data else "\n"
        first = (self.shebang.get().strip() + newline)
        if old: lines[0] = first
        else: lines.insert(0, first)
        if self.blank_line.get() and (len(lines) < 2 or lines[1].strip()): lines.insert(1, newline)
        return "".join(lines).encode("utf-8", errors="surrogateescape")

    def shebang_preview(self):
        if not self.files: self.scan()
        if not self.shebang.get().startswith("#!"): return messagebox.showwarning("Shebang", "Shebang must start with #!")
        for path in self.files:
            try:
                original = path.read_bytes()
                self.log(f"{'Change' if self._new_shebang(original) != original else 'Skip'}: {path}")
            except OSError as exc: self.log(str(exc))

    def shebang_apply(self):
        if not self.files: self.scan()
        if not self.files: return
        if not self.shebang.get().startswith("#!"): return messagebox.showwarning("Shebang", "Shebang must start with #!")
        root = self.project_path()
        if self.copy_shebang.get():
            target = Path(self.shebang_out.get()).expanduser()
            if not self.shebang_out.get() or target.resolve() == root.resolve() or root.resolve() in target.resolve().parents:
                return messagebox.showwarning("Output", "Choose an output folder outside the source project")
        elif not messagebox.askyesno("Edit originals", f"Modify {len(self.files)} original files with .bak backups?"): return
        for path in self.files:
            try:
                original = path.read_bytes()
                updated = self._new_shebang(original)
                if self.copy_shebang.get():
                    dest = target / path.relative_to(root)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists(): self.log(f"Skip existing output: {dest}"); continue
                    shutil.copy2(path, dest)
                else:
                    dest = path
                    if updated != original: shutil.copy2(path, path.with_name(path.name + ".bak"))
                if updated != original: dest.write_bytes(updated)
                if self.make_executable.get() and os.name != "nt": dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
                self.log(f"{'Updated' if updated != original else 'Unchanged'}: {dest}")
            except OSError as exc: self.log(f"Error: {path}: {exc}")

    def build(self, module, args):
        try:
            python = self.python_path()
            if module == "cx_Freeze":
                setup = self.project_path() / "setup.py"
                if not setup.is_file(): raise ValueError("cx_Freeze needs a setup.py in the project")
                command = [python, str(setup), "build"]
            else: command = [python, "-m", module, *args, self.script_path()]
            self.run(command)
        except ValueError as exc: messagebox.showwarning("Build", str(exc))


if __name__ == "__main__":
    Workbench().mainloop()
