"""
PixelPal — A cute desktop pet that lives on your screen.
Built with tkinter + standard library only. No external images needed.
"""

import tkinter as tk
import math
import random
import json
import os
import sys

# ---------------------------------------------------------------------------
# Path helpers (PyInstaller-compatible)
# ---------------------------------------------------------------------------

def resource_path(relative: str) -> str:
    """Return absolute path to a bundled resource (PyInstaller _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)

def data_dir() -> str:
    """Return writable directory for user data (%APPDATA%/PixelPal on Windows)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~")
    path = os.path.join(base, "PixelPal")
    os.makedirs(path, exist_ok=True)
    return path

SAVE_FILE = os.path.join(data_dir(), "pet_data.json")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PET_W, PET_H = 160, 150          # canvas / window size (extra headroom for speech bubbles)
FPS = 40                          # target frames per second
FRAME_MS = 1000 // FPS            # ms per frame
GRAVITY = 0.6                     # pixels / frame²
BOUNCE_DAMP = 0.45                # velocity kept after bounce
TASKBAR_H = 48                    # approximate Windows taskbar height
FOLLOW_DIST = 8                   # stop following when this close
IDLE_TIMEOUT = 60                 # seconds before sleep

# Pastel colour palettes  (body, belly, feet, shadow)
PALETTES = {
    "Peach":   ("#FFB7A5", "#FFD9CC", "#E8967E", "#D4A090"),
    "Mint":    ("#A8E6CF", "#D4F5E4", "#7ECBA1", "#8FBFA8"),
    "Lilac":   ("#C9B1FF", "#E2D4FF", "#A78BDB", "#A99CC4"),
    "Sky":     ("#A0D2DB", "#C8E6EC", "#7BB4BF", "#8DA8AE"),
    "Butter":  ("#FFE49C", "#FFF1CC", "#D4BC6E", "#C4B07A"),
}
DEFAULT_COLOR = "Peach"
DEFAULT_NAME  = "Blobby"

# ---------------------------------------------------------------------------
# Main application class
# ---------------------------------------------------------------------------

class PixelPalApp:
    """Desktop pet with state machine, mood, physics and persistence."""

    # ---- setup -----------------------------------------------------------

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PixelPal")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Transparent background colour (must not appear anywhere on pet)
        self._trans = "#010101"
        self.root.config(bg=self._trans)
        try:
            self.root.attributes("-transparentcolor", self._trans)
        except tk.TclError:
            pass  # non-Windows fallback: no transparency

        # Screen dimensions
        self.scr_w = self.root.winfo_screenwidth()
        self.scr_h = self.root.winfo_screenheight()
        self.ground_y = self.scr_h - TASKBAR_H - PET_H

        # Canvas
        self.canvas = tk.Canvas(
            self.root, width=PET_W, height=PET_H,
            bg=self._trans, highlightthickness=0,
        )
        self.canvas.pack()

        # Position (start at random bottom-screen spot)
        self.x = random.randint(100, self.scr_w - PET_W - 100)
        self.y = self.ground_y
        self.root.geometry(f"{PET_W}x{PET_H}+{self.x}+{self.y}")

        # Physics
        self.vx = 0.0
        self.vy = 0.0

        # State
        self.state = "IDLE"
        self.prev_state = "IDLE"
        self.state_timer = 0       # frames in current state
        self.idle_timer  = 0.0     # seconds since last interaction
        self.walk_dir    = 1       # 1 = right, -1 = left
        self.walk_speed  = 1.5
        self.facing      = 1       # 1 = right, -1 = left

        # Animation helpers
        self.frame = 0             # global frame counter
        self.blink_timer = random.randint(80, 200)
        self.is_blinking  = False
        self.blink_frames = 0
        self.squash = 0.0          # -1..1 squash-stretch factor
        self.bob    = 0.0          # vertical bob while walking

        # Mood
        self.hunger    = 80.0
        self.happiness = 80.0

        # Pet identity
        self.pet_name  = DEFAULT_NAME
        self.color_key = DEFAULT_COLOR

        # Particles (hearts, Zzz, speech)
        self.particles = []        # list of dicts

        # Speech bubble
        self.speech_text  = ""
        self.speech_timer = 0      # frames remaining
        self.speech_cooldown = 0   # frames before next speech

        # Drag state
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        # Load saved data
        self._load()

        # Bindings
        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",        self._on_drag)
        self.canvas.bind("<ButtonRelease-1>",  self._on_release)
        self.canvas.bind("<ButtonPress-3>",    self._on_right_click)

        # Start loops
        self._tick()
        self._mood_tick()

    # ---- persistence -----------------------------------------------------

    def _load(self):
        try:
            with open(SAVE_FILE, "r") as f:
                data = json.load(f)
            self.pet_name   = data.get("name",      DEFAULT_NAME)
            self.color_key  = data.get("color",     DEFAULT_COLOR)
            self.hunger     = data.get("hunger",    80.0)
            self.happiness  = data.get("happiness", 80.0)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        data = {
            "name":      self.pet_name,
            "color":     self.color_key,
            "hunger":    round(self.hunger, 1),
            "happiness": round(self.happiness, 1),
        }
        try:
            with open(SAVE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # ---- input handlers --------------------------------------------------

    def _on_press(self, event):
        self.idle_timer = 0.0
        if self.state == "SLEEP":
            self._wake_up()
            return
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y
        self._set_state("DRAGGED")

    def _on_drag(self, event):
        if self.state != "DRAGGED":
            return
        nx = self.root.winfo_x() + (event.x - self._drag_offset_x)
        ny = self.root.winfo_y() + (event.y - self._drag_offset_y)
        self.x = nx
        self.y = ny
        self.root.geometry(f"+{self.x}+{self.y}")

    def _on_release(self, event):
        if self.state != "DRAGGED":
            return
        self.idle_timer = 0.0
        if self.y < self.ground_y - 10:
            self.vy = 0
            self._set_state("FALL")
        else:
            self._trigger_happy()

    def _on_right_click(self, event):
        self.idle_timer = 0.0
        if self.state == "SLEEP":
            self._wake_up()
        menu = tk.Menu(self.root, tearoff=0, font=("Segoe UI", 10))
        menu.add_command(label=f"🐾 {self.pet_name}", state="disabled")
        menu.add_separator()
        menu.add_command(label="🍎 Feed",           command=self._feed)
        menu.add_command(label="🎾 Play",           command=self._play)
        menu.add_command(label="👆 Follow my mouse", command=self._start_follow)
        # Colour submenu
        color_menu = tk.Menu(menu, tearoff=0, font=("Segoe UI", 10))
        for key in PALETTES:
            color_menu.add_command(
                label=("● " if key == self.color_key else "  ") + key,
                command=lambda k=key: self._change_color(k),
            )
        menu.add_cascade(label="🎨 Change color", menu=color_menu)
        menu.add_separator()
        menu.add_command(label="✏️ Rename", command=self._rename_dialog)
        menu.add_command(label="❌ Quit", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---- menu actions ----------------------------------------------------

    def _feed(self):
        self.hunger = min(100.0, self.hunger + 25)
        self._show_speech("Yum yum! 🍎")
        self._trigger_happy()

    def _play(self):
        self.happiness = min(100.0, self.happiness + 25)
        self._show_speech("Wheee! 🎉")
        self._trigger_happy()

    def _start_follow(self):
        self._set_state("FOLLOW")
        self._show_speech("Coming!")

    def _change_color(self, key):
        self.color_key = key
        self._save()

    def _rename_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Rename your PixelPal")
        dlg.geometry("280x110")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        tk.Label(dlg, text="Enter a new name:", font=("Segoe UI", 11)).pack(pady=(12, 4))
        entry = tk.Entry(dlg, font=("Segoe UI", 12), width=18, justify="center")
        entry.insert(0, self.pet_name)
        entry.pack()
        entry.select_range(0, tk.END)
        entry.focus_set()

        def confirm(_=None):
            name = entry.get().strip()
            if name:
                self.pet_name = name[:16]
                self._save()
                self._show_speech(f"Call me {self.pet_name}!")
            dlg.destroy()

        entry.bind("<Return>", confirm)
        tk.Button(dlg, text="OK", command=confirm, width=8).pack(pady=8)

    def _quit(self):
        self._save()
        self.root.destroy()

    # ---- state machine ---------------------------------------------------

    def _set_state(self, new_state):
        self.prev_state = self.state
        self.state = new_state
        self.state_timer = 0

    def _trigger_happy(self):
        self._set_state("HAPPY")
        # Spawn heart particles
        for _ in range(5):
            self.particles.append({
                "type": "heart",
                "x": PET_W // 2 + random.randint(-18, 18),
                "y": 20,
                "vy": -random.uniform(0.6, 1.8),
                "vx": random.uniform(-0.4, 0.4),
                "life": random.randint(40, 70),
                "max_life": 70,
                "size": random.randint(6, 10),
            })

    def _wake_up(self):
        self._set_state("IDLE")
        self._show_speech("*yawn* Good morning!")
        self.idle_timer = 0.0

    # ---- mood tick (every 5 s) -------------------------------------------

    def _mood_tick(self):
        if self.state != "SLEEP":
            self.hunger    = max(0, self.hunger - 0.4)
            self.happiness = max(0, self.happiness - 0.3)
        else:
            self.hunger    = max(0, self.hunger - 0.15)
            self.happiness = max(0, self.happiness - 0.08)

        # Speech prompts based on mood
        if self.speech_cooldown <= 0 and self.state not in ("SLEEP", "DRAGGED", "FALL"):
            if self.hunger < 25:
                self._show_speech("I'm hungry! 🍎")
                self.speech_cooldown = FPS * 30
            elif self.happiness < 25:
                self._show_speech("Play with me! 🎾")
                self.speech_cooldown = FPS * 30
            elif random.random() < 0.12:
                msgs = [
                    "Hi there! 👋", "Nice day~", "Hehe ✨",
                    f"I'm {self.pet_name}!", "What'cha doin?",
                    "La la la~♪", "Pet me! 🐾",
                ]
                self._show_speech(random.choice(msgs))
                self.speech_cooldown = FPS * 20

        self._save()
        self.root.after(5000, self._mood_tick)

    # ---- main tick -------------------------------------------------------

    def _tick(self):
        self.frame += 1
        self.state_timer += 1
        self.idle_timer += FRAME_MS / 1000.0
        if self.speech_cooldown > 0:
            self.speech_cooldown -= 1

        # Blink logic
        self.blink_timer -= 1
        if self.blink_timer <= 0 and not self.is_blinking:
            self.is_blinking = True
            self.blink_frames = 5
        if self.is_blinking:
            self.blink_frames -= 1
            if self.blink_frames <= 0:
                self.is_blinking = False
                self.blink_timer = random.randint(80, 200)

        # State updates
        if self.state == "IDLE":
            self._update_idle()
        elif self.state == "WALK":
            self._update_walk()
        elif self.state == "FOLLOW":
            self._update_follow()
        elif self.state == "HAPPY":
            self._update_happy()
        elif self.state == "FALL":
            self._update_fall()
        elif self.state == "DRAGGED":
            self._update_dragged()
        elif self.state == "SLEEP":
            self._update_sleep()

        # Sleep check
        if self.state in ("IDLE", "WALK") and self.idle_timer >= IDLE_TIMEOUT:
            self._set_state("SLEEP")
            self._show_speech("Zzz...")

        # Follow-trigger: cursor near pet for a moment
        if self.state in ("IDLE", "WALK"):
            mx, my = self.root.winfo_pointerxy()
            dx = mx - (self.x + PET_W // 2)
            dy = my - (self.y + PET_H // 2)
            if math.hypot(dx, dy) < 60:
                if not hasattr(self, "_near_timer"):
                    self._near_timer = 0
                self._near_timer += 1
                if self._near_timer > FPS * 2:
                    self._set_state("FOLLOW")
                    self._near_timer = 0
            else:
                self._near_timer = 0

        # Clamp position
        self.x = max(-PET_W // 2, min(self.scr_w - PET_W // 2, self.x))
        self.y = min(self.ground_y, self.y)
        self.root.geometry(f"+{int(self.x)}+{int(self.y)}")

        # Update particles
        self._update_particles()

        # Draw
        self._draw()

        # Speech bubble timer
        if self.speech_timer > 0:
            self.speech_timer -= 1

        self.root.after(FRAME_MS, self._tick)

    # ---- state updaters --------------------------------------------------

    def _update_idle(self):
        self.squash = math.sin(self.frame * 0.06) * 0.04
        self.bob = 0
        # Randomly start walking
        if self.state_timer > FPS * random.randint(2, 5):
            self.walk_dir = random.choice([-1, 1])
            self.facing = self.walk_dir
            self.walk_speed = random.uniform(1.0, 2.2)
            self._set_state("WALK")

    def _update_walk(self):
        # Walking animation
        t = self.frame * 0.18
        self.squash = math.sin(t) * 0.08
        self.bob = abs(math.sin(t)) * 4

        self.x += self.walk_dir * self.walk_speed

        # Turn around at edges
        if self.x <= 10:
            self.walk_dir = 1
            self.facing = 1
        elif self.x >= self.scr_w - PET_W - 10:
            self.walk_dir = -1
            self.facing = -1

        # Stop after a while
        if self.state_timer > FPS * random.randint(3, 8):
            self._set_state("IDLE")

    def _update_follow(self):
        mx, my = self.root.winfo_pointerxy()
        target_x = mx - PET_W // 2
        dx = target_x - self.x
        dist = abs(dx)

        if dist < FOLLOW_DIST:
            self.squash = math.sin(self.frame * 0.06) * 0.04
            self.bob = 0
            # Stop following after inactivity
            if self.state_timer > FPS * 10:
                self._set_state("IDLE")
            return

        speed = min(4.0, dist * 0.06)
        self.facing = 1 if dx > 0 else -1
        self.x += speed * self.facing

        t = self.frame * 0.22
        self.squash = math.sin(t) * 0.10
        self.bob = abs(math.sin(t)) * 5

        self.state_timer = 0  # reset while actively chasing

    def _update_happy(self):
        self.squash = math.sin(self.frame * 0.3) * 0.12
        self.bob = abs(math.sin(self.frame * 0.3)) * 6
        if self.state_timer > FPS * 2:
            self._set_state("IDLE")

    def _update_fall(self):
        self.vy += GRAVITY
        self.y += self.vy
        self.squash = -0.12  # stretched vertically while falling

        if self.y >= self.ground_y:
            self.y = self.ground_y
            if abs(self.vy) > 2:
                self.vy = -self.vy * BOUNCE_DAMP
                self.squash = 0.20  # squashed on impact
            else:
                self.vy = 0
                self._set_state("IDLE")

    def _update_dragged(self):
        self.squash = 0.10  # squished while held
        self.bob = math.sin(self.frame * 0.15) * 2

    def _update_sleep(self):
        self.squash = math.sin(self.frame * 0.03) * 0.03
        self.bob = 0
        # Floating Zzz particles
        if self.frame % 50 == 0:
            self.particles.append({
                "type": "zzz",
                "x": PET_W // 2 + 15,
                "y": 20,
                "vy": -0.5,
                "vx": random.uniform(0.1, 0.4),
                "life": 80,
                "max_life": 80,
                "size": random.randint(9, 13),
            })

    # ---- particles -------------------------------------------------------

    def _update_particles(self):
        alive = []
        for p in self.particles:
            p["x"] += p.get("vx", 0)
            p["y"] += p["vy"]
            p["life"] -= 1
            if p["life"] > 0:
                alive.append(p)
        self.particles = alive

    # ---- speech ----------------------------------------------------------

    def _show_speech(self, text):
        self.speech_text = text
        self.speech_timer = FPS * 4  # show for ~4 seconds

    # ---- drawing ---------------------------------------------------------

    def _draw(self):
        c = self.canvas
        c.delete("all")

        pal = PALETTES.get(self.color_key, PALETTES[DEFAULT_COLOR])
        body_col, belly_col, feet_col, shadow_col = pal

        cx, cy_base = PET_W // 2, PET_H - 24  # base centre of body

        # Squash / stretch transform factors
        sx = 1.0 + self.squash
        sy = 1.0 - self.squash
        bob = self.bob

        # Shadow
        c.create_oval(
            cx - 28 * sx, cy_base + 12,
            cx + 28 * sx, cy_base + 20,
            fill=shadow_col, outline="",
        )

        # Feet (behind body) -  two little ovals
        foot_spread = 14 * sx
        foot_y = cy_base + 4 - bob
        if self.state == "DRAGGED":
            # dangling feet
            foot_y = cy_base + 10 + abs(math.sin(self.frame * 0.15)) * 4
        elif self.state == "SLEEP":
            foot_y = cy_base + 6
            foot_spread = 18

        # Walk animation offset
        foot_anim = math.sin(self.frame * 0.18) * 4 if self.state in ("WALK", "FOLLOW") else 0
        c.create_oval(
            cx - foot_spread - 8, foot_y - 6 + foot_anim,
            cx - foot_spread + 4, foot_y + 4 + foot_anim,
            fill=feet_col, outline="",
        )
        c.create_oval(
            cx + foot_spread - 4, foot_y - 6 - foot_anim,
            cx + foot_spread + 8, foot_y + 4 - foot_anim,
            fill=feet_col, outline="",
        )

        # Body blob
        body_rx = 30 * sx
        body_ry = 32 * sy
        body_top = cy_base - body_ry * 2 - bob
        body_bot = cy_base + 4 - bob

        if self.state == "SLEEP":
            # Flatten body for sleeping
            body_rx = 36
            body_ry = 20
            body_top = cy_base - body_ry - 4
            body_bot = cy_base + 10

        c.create_oval(
            cx - body_rx, body_top,
            cx + body_rx, body_bot,
            fill=body_col, outline="",
        )

        # Belly highlight
        belly_rx = body_rx * 0.55
        belly_ry = body_ry * 0.45
        belly_cy = (body_top + body_bot) / 2 + body_ry * 0.25
        c.create_oval(
            cx - belly_rx, belly_cy - belly_ry,
            cx + belly_rx, belly_cy + belly_ry,
            fill=belly_col, outline="",
        )

        # Face centre
        face_cy = (body_top + body_bot) / 2 - body_ry * 0.15

        # Eyes
        eye_sep = 10 * self.facing  # shift slightly toward facing direction
        eye_cx_l = cx - 9 + eye_sep * 0.15
        eye_cx_r = cx + 9 + eye_sep * 0.15
        eye_cy = face_cy - 2

        if self.state == "SLEEP":
            # Closed eyes (arcs)
            for ex in (eye_cx_l, eye_cx_r):
                c.create_arc(
                    ex - 5, eye_cy - 3, ex + 5, eye_cy + 5,
                    start=0, extent=180, style="arc",
                    outline="#555", width=2,
                )
        elif self.is_blinking:
            for ex in (eye_cx_l, eye_cx_r):
                c.create_line(ex - 4, eye_cy, ex + 4, eye_cy, fill="#555", width=2)
        elif self.state == "DRAGGED":
            # Wide eyes
            for ex in (eye_cx_l, eye_cx_r):
                c.create_oval(ex - 6, eye_cy - 7, ex + 6, eye_cy + 7,
                              fill="white", outline="#555", width=1)
                c.create_oval(ex - 3, eye_cy - 3, ex + 3, eye_cy + 3,
                              fill="#333", outline="")
        else:
            # Normal eyes
            for ex in (eye_cx_l, eye_cx_r):
                c.create_oval(ex - 5, eye_cy - 5, ex + 5, eye_cy + 5,
                              fill="white", outline="#888", width=1)
                # Pupil — shift toward facing
                px = ex + self.facing * 1.5
                c.create_oval(px - 2.5, eye_cy - 2.5, px + 2.5, eye_cy + 2.5,
                              fill="#333", outline="")
                # Highlight
                c.create_oval(px - 4, eye_cy - 4, px - 1, eye_cy - 1,
                              fill="white", outline="")

        # Cheeks (blush)
        if self.state in ("HAPPY", "FOLLOW"):
            for bx_off in (-16, 16):
                c.create_oval(
                    cx + bx_off - 5, face_cy + 5,
                    cx + bx_off + 5, face_cy + 11,
                    fill="#FFB3B3", outline="",
                )

        # Mouth
        mouth_cy = face_cy + 10
        if self.state == "HAPPY":
            c.create_arc(
                cx - 7, mouth_cy - 5, cx + 7, mouth_cy + 7,
                start=200, extent=140, style="arc",
                outline="#777", width=2,
            )
        elif self.state == "SLEEP":
            # Tiny o
            c.create_oval(cx - 3, mouth_cy, cx + 3, mouth_cy + 4,
                          fill="#C09090", outline="")
        elif self.state == "DRAGGED":
            # Surprised O
            c.create_oval(cx - 4, mouth_cy - 2, cx + 4, mouth_cy + 5,
                          fill="#C09090", outline="#999", width=1)
        else:
            # Gentle smile
            c.create_arc(
                cx - 5, mouth_cy - 4, cx + 5, mouth_cy + 4,
                start=210, extent=120, style="arc",
                outline="#888", width=1.5,
            )

        # ---- Mood bars (tiny, above pet) ---------------------------------
        bar_y = max(4, body_top - 14)
        bar_w = 40
        bar_h = 5
        for i, (val, col) in enumerate([(self.hunger, "#FF9B9B"), (self.happiness, "#FFD36E")]):
            bx = cx - bar_w // 2 + (i * (bar_w + 6)) - 22
            # background
            c.create_rectangle(bx, bar_y, bx + bar_w, bar_y + bar_h,
                               fill="#E0E0E0", outline="#CCC", width=1)
            # fill
            fw = max(1, bar_w * val / 100)
            c.create_rectangle(bx, bar_y, bx + fw, bar_y + bar_h,
                               fill=col, outline="")
            # icon
            icon = "🍎" if i == 0 else "★"
            c.create_text(bx - 7, bar_y + 2, text=icon, font=("Segoe UI", 6),
                          anchor="center")

        # ---- Particles ---------------------------------------------------
        for p in self.particles:
            alpha = max(0.0, p["life"] / p["max_life"])
            if p["type"] == "heart":
                self._draw_heart(c, p["x"], p["y"], p["size"], alpha)
            elif p["type"] == "zzz":
                grey = int(180 + 75 * (1 - alpha))
                col = f"#{grey:02x}{grey:02x}{grey:02x}"
                c.create_text(
                    p["x"], p["y"], text="Z",
                    font=("Segoe UI", p["size"], "bold"),
                    fill=col, anchor="center",
                )

        # ---- Speech bubble -----------------------------------------------
        if self.speech_timer > 0 and self.speech_text:
            self._draw_speech(c, cx, body_top - 16)

    # ---- helper draw methods ---------------------------------------------

    def _draw_heart(self, c, x, y, size, alpha):
        """Draw a simple heart shape using two overlapping ovals + triangle."""
        r = size / 2
        # Fade colour from red to pink
        red = int(255 * alpha)
        col = f"#{red:02x}{int(80 * alpha):02x}{int(80 * alpha):02x}"
        c.create_oval(x - r, y - r, x, y + r * 0.3, fill=col, outline="")
        c.create_oval(x, y - r, x + r, y + r * 0.3, fill=col, outline="")
        c.create_polygon(
            x - r, y, x + r, y,
            x, y + r * 1.4,
            fill=col, outline="",
        )

    def _draw_speech(self, c, cx, bottom_y):
        """Draw rounded speech bubble above pet safely inside canvas bounds."""
        text = self.speech_text
        font = ("Segoe UI", 9)

        # Measure text width (approximate)
        est_w = len(text) * 7 + 22
        bw = max(64, min(est_w, PET_W - 16))
        bh = 24

        # Calculate coordinates with safety margins
        bx = max(8, min(cx - bw // 2, PET_W - bw - 8))
        by = max(4, bottom_y - bh - 6)
        r = 8  # corner radius

        # Fade in / out
        if self.speech_timer < 10:
            opacity_frac = self.speech_timer / 10.0
        elif self.speech_timer > FPS * 4 - 10:
            opacity_frac = (FPS * 4 - self.speech_timer) / 10.0
        else:
            opacity_frac = 1.0
        opacity_frac = max(0.0, min(1.0, opacity_frac))

        bg = "white"
        outline = "#CCC"

        # Rounded rect (using polygon approximation)
        points = [
            bx + r, by,
            bx + bw - r, by,
            bx + bw, by,
            bx + bw, by + r,
            bx + bw, by + bh - r,
            bx + bw, by + bh,
            bx + bw - r, by + bh,
            bx + r, by + bh,
            bx, by + bh,
            bx, by + bh - r,
            bx, by + r,
            bx, by,
        ]
        c.create_polygon(points, fill=bg, outline=outline, smooth=True, width=1)

        # Tail triangle pointing towards pet center
        tx = max(bx + 12, min(cx, bx + bw - 12))
        c.create_polygon(
            tx - 5, by + bh,
            tx + 5, by + bh,
            tx, by + bh + 6,
            fill=bg, outline="",
        )
        # Cover the outline at tail base
        c.create_line(tx - 5, by + bh, tx + 5, by + bh, fill=bg, width=2)

        # Text
        grey = int(80 + 175 * (1 - opacity_frac))
        text_col = f"#{grey:02x}{grey:02x}{grey:02x}"
        c.create_text(
            bx + bw // 2, by + bh // 2, text=text,
            font=font, fill=text_col, anchor="center",
        )

    # ---- run -------------------------------------------------------------

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = PixelPalApp()
    app.run()
