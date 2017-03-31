import os
import sys
import keyboard
import autorun
import psutil
from enum import IntEnum
from PIL import Image

import win32api
import win32gui
import win32con

APP_NAME = 'TydiWin'
EXEC_PATH = os.path.dirname(sys.executable)
CONF_FILE_NAME = os.path.join(EXEC_PATH, 'conf.txt')
_grid_index = 0
_rotate_index = 0
_prev_layout = None

class NoMoreRotations(Exception): pass
class MultipleInstancesNotAllowed(Exception): pass

# create the logger
import logging
logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.DEBUG)
sys.stderr = sys.stdout # supress the error show popup at close

fh = logging.FileHandler(os.path.join(EXEC_PATH, "log.txt"))
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message).4096s')
fh.setFormatter(file_formatter)
logger.addHandler(fh)

# add console handler
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message).4096s')
ch = logging.StreamHandler()
ch.setFormatter(console_formatter)
logger.addHandler(ch)

class WindowState(IntEnum):
    NORMAL = 1
    MINIMIZED = 6
    MAXIMIZED = 3

class MyRect(object):
    def __init__(self, rect_tpl):
        self.left = int(rect_tpl[0])
        self.top = int(rect_tpl[1])
        self.right = int(rect_tpl[2])
        self.bottom = int(rect_tpl[3])

        self.width = self.right-self.left
        self.height = self.bottom-self.top

    def __eq__(self, other):
        return self.left == other.left and self.right == other.right and \
            self.bottom == other.bottom and self.top == other.top

    def __repr__(self):
        return str((self.left, self.top, self.right, self.bottom,))

    def intersection_area(self, b):
        a = self
        dx = min(a.right, b.right) - max(a.left, b.left)
        dy = min(a.bottom, b.bottom) - max(a.top, b.top)
        if (dx>=0) and (dy>=0):
            return dx*dy

class MyWindow(object):

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.text = win32gui.GetWindowText(hwnd)
        self.rect = MyRect(win32gui.GetWindowRect(hwnd))
        self.screen_rect = win32gui.ClientToScreen(hwnd, (self.rect.left, self.rect.top)) 

    def __eq__(self, other):
        return self.hwnd == other.hwnd and self.rect == other.rect

    def __repr__(self):
        return "hwnd=%s, text=%s, rect=%s" % (self.hwnd, self.text, self.rect)

    def set_rect(self, rect):
        logger.debug("set_rect(%s) called for rect(%s)" % (rect, self.rect))

        self.restore()

        win32gui.MoveWindow(self.hwnd, 
            rect.left, rect.top, rect.width, rect.height, True)

    def maximize(self):
        win32gui.ShowWindow(self.hwnd, win32con.SW_MAXIMIZE)

    def restore(self):
        win32gui.ShowWindow(self.hwnd, win32con.SW_NORMAL)

    @property
    def left(self):
        return self.rect.left

    @property
    def top(self):
        return self.rect.top

    @property
    def right(self):
        return self.rect.right

    @property
    def bottom(self):
        return self.rect.bottom

    @property
    def width(self):
        return self.rect.width

    @property
    def height(self):
        return self.rect.height

    @property
    def monitor(self):
        return get_monitor_from_rect(self.rect)

    @property
    def style(self):
        return win32api.GetWindowLong(self.hwnd, win32con.GWL_STYLE)

    @property
    def is_resizable(self):
        return (self.style & win32con.WS_THICKFRAME == win32con.WS_THICKFRAME)

    @property
    def placement(self):
        p_tpl = win32gui.GetWindowPlacement(self.hwnd)
        if p_tpl[1] == win32con.SW_MAXIMIZE:
            return WindowState.MAXIMIZED
        elif p_tpl[1] == win32con.SW_MINIMIZE:
            return WindowState.MINIMIZED
        elif p_tpl[1] == win32con.SW_NORMAL:
            return WindowState.NORMAL

def get_current_window():
    hwnd = win32gui.GetForegroundWindow()
    return MyWindow(hwnd)

def load_configuration_module():
    # TODO: Check if conf is correct by validating lists/dicts and window counts have
    # at least window count entries or keyboard entries
    import builtins
    cmon = get_current_window().monitor
    builtins._MonitorWidth = cmon.width
    builtins._MonitorHeight = cmon.height
    import imp; imp.load_source('configuration', CONF_FILE_NAME)
    import configuration

    # refresh hotkeys
    keyboard.clear_all_hotkeys()
    keyboard.add_hotkey(configuration.KEYMAPS["move_to_next_monitor"], move_to_next_monitor)
    keyboard.add_hotkey(configuration.KEYMAPS["tidy_monitor"], tidy_monitor)

    logger.debug("Hotkeys installed: %s" % (configuration.KEYMAPS))
    
    return configuration

class MyMonitorLayout(object):
    def __init__(self):

        # TODO: This is called too much, can be optimized at least a snapshot per
        # operation

        self._monitors = []

        for m in win32api.EnumDisplayMonitors():
            mon_info = win32api.GetMonitorInfo(m[0])
            working_rect = mon_info['Work']
            mon = Monitor(m[2], working_rect)
            if mon_info['Flags'] == 1:
                self._primary = mon

            # make monitors linked-list
            if len(self._monitors) > 0:
                mon.set_next(self._monitors[-1])

            self._monitors.append(mon)
        
        if len(self._monitors) > 1:
            self._monitors[0].set_next(self._monitors[1])

        logger.debug("Current Monitor Layout: %s" % (self._monitors))

    def __iter__(self):
        for m in self._monitors:
            yield m

    def __repr__(self):
        return str(self._monitors)

    @property
    def primary(self):
        return self._primary

    @property
    def current_monitor(self):
        return get_current_window().monitor

def rotate(l, n):
    return l[-n:] + l[:-n]

class MyWindowLayout(object):
    def __init__(self, monitor):
        self.windows = {}
        self.windows_as_list = []
        self.monitor = monitor

        def enum_wnd(hwnd, layout):
            EXCLUDE_WINDOW_TITLES = ["Start", "Program Manager", ""]
            if not win32gui.IsIconic(hwnd) and win32gui.IsWindowVisible(hwnd):
                wnd = MyWindow(hwnd)

                # Exclude some Windows apps
                if wnd.text in EXCLUDE_WINDOW_TITLES:
                    return

                if wnd.is_resizable is False:
                    return

                # top-level windows should have desktop as background
                #phwnd = win32gui.GetParent(hwnd)        
                #if phwnd != 0:          
                #   return

                wnd_mon = get_monitor_from_rect(wnd.rect)
                if wnd_mon == layout.monitor:
                    #logger.debug("WE FIND Window(%s) in current Monitor" % (wnd.text))
                    layout.add_window(wnd) 

        win32gui.EnumWindows(enum_wnd, self)

    def add_window(self, wnd):
        self.windows[wnd.hwnd] = wnd
        self.windows_as_list.append(wnd.hwnd)

    @property
    def window_count(self):
        return len(self.windows)

    def __eq__(self, other):
        if other is None or len(self.windows) != len(other.windows):
            return False

        for k,v in self.windows.items():
            if k not in other.windows:
                return False
            if other.windows[k] != v:
                return False

        #import traceback;traceback.print_stack()
        return True

    def tidy(self, grid_conf):
        assert(grid_conf is not None)
        assert(len(self.windows) > 0)

        logger.debug("TIDY Called.(gci=%s)" % (grid_conf))

        for i,hwnd in enumerate(self.windows_as_list[:len(grid_conf)]):
            w = self.windows[hwnd]
            rt = grid_conf[i]
            wnd_rect = MyRect((rt[0]+self.monitor.working_rect.left, rt[1]+self.monitor.working_rect.top, 
                rt[0]+rt[2]+self.monitor.working_rect.left, rt[1]+rt[3]+self.monitor.working_rect.top,))
            w.set_rect(wnd_rect)

    def rotate(self, val):
        assert(len(self.windows) > 0)

        if val == self.window_count: # rotations finished?
            raise NoMoreRotations("")

        logger.debug("ROTATE Called.(rc=%d)" % (val))

        self.windows_as_list = rotate(self.windows_as_list, val)
        
    def __repr__(self):
        return str(self.windows)

class Monitor(object):
    def __init__(self, rect, working_rect):
        self.rect = MyRect(rect)
        self.working_rect = MyRect(working_rect)
        
        self.width = self.working_rect.width
        self.height = self.working_rect.height

        self.next = self

    def set_next(self, mon):
        self.next = mon

    @property
    def window_layout(self):
        return MyWindowLayout(self)
        
    def __eq__(self, other):
        if other is None:
            return False
        return (self.working_rect == other.working_rect)

    def __repr__(self):
        return "%s, next_left=%s" % (self.rect, self.next.rect.left)

def get_monitor_from_rect(rect):
    """Selects the monitor based on the maximum intersecting area of the rect to 
    the monitor. This is because some windows x/y coords can be out of bounds of 
    the monitor by some pixels but it is mostly on that monitor"""
    max_area_so_far = 0
    monitor_layout = MyMonitorLayout()

    # sometimes window can be in non-visible area of the monitor, for some popups
    # I see that behavior, return the primary monitor for those cases.
    result = monitor_layout.primary
    for m in monitor_layout:
        iarea = m.working_rect.intersection_area(rect)
        if iarea is not None and iarea > max_area_so_far:
            max_area_so_far = iarea
            result = m

    return result

def move_window_to_next_mon(wnd):

    # TODO: NOT-TESTED: 3 screens..etc.

    # if window is maximized, first restore it and then move and maximize
    wnd_maximized_before = False
    if wnd.placement == WindowState.MAXIMIZED:
        wnd.restore()
        wnd_maximized_before = True

    cur_mon = wnd.monitor
    next_mon = cur_mon.next 

    # we find the current mon., move by adding width to left
    new_left = cur_mon.next.working_rect.left + (wnd.left - cur_mon.working_rect.left)
    new_top = cur_mon.next.working_rect.top + (wnd.top - cur_mon.working_rect.top)
    
    # if left or top exceeds mon. wid/hei then truncate the wnd right/bottom 
    # to mon. wid/hei
    new_left = max(min(new_left, next_mon.working_rect.right-wnd.width), 
        next_mon.working_rect.left)
    new_top = max(min(new_top, next_mon.working_rect.bottom-wnd.height), 
        next_mon.working_rect.top)
    new_rect = MyRect((new_left, new_top, new_left+wnd.width, new_top+wnd.height,))

    # move
    wnd.set_rect(new_rect)

    # maximize if restored
    if wnd_maximized_before:
        wnd.maximize()

def move_to_next_monitor():
    # may be conf. file is changed, (i.e: keymaps changed)
    load_configuration_module()

    # DisplayFusion: get left/top and move to next mon with left appended if width reached,
    # simply add that right will be right of the monitor do not crop the window
    move_window_to_next_mon(get_current_window())
    
def tidy_monitor():
    global _rotate_index, _grid_index, _prev_layout
    
    cur_layout = MyMonitorLayout().current_monitor.window_layout # reload all layouts
    conf = load_configuration_module() # re-load conf -- it might be changed

    if cur_layout.window_count < 1:
        return

    # if we have only one window and it is already maximized, do not
    # maximize it.
    if cur_layout.window_count == 1 and get_current_window().placement == WindowState.MAXIMIZED:
        return

    # trim window_count to max avaible grid conf count
    wnd_count = cur_layout.window_count
    if cur_layout.window_count > len(conf.GRIDS):
        wnd_count = len(conf.GRIDS)
        logger.info("Trimmed %d window count to %d" % (cur_layout.window_count, 
            len(conf.GRIDS)))

    grid_conf = None
    if _prev_layout == cur_layout:
        _rotate_index += 1
        try:
            cur_layout.rotate(_rotate_index)
        except NoMoreRotations:
            _rotate_index = 0

            # TODO: instead of retriving next in list, retrieve the recently used
            # grid conf
            _grid_index = (_grid_index + 1) % (len(conf.GRIDS[wnd_count]))
    else:
        _grid_index = 0
        _rotate_index = 0
        _prev_layout = None

    # gci and ri calculated, tidy() can be called
    grid_conf = conf.GRIDS[wnd_count][_grid_index]
    cur_layout.tidy(grid_conf)

    _prev_layout = MyMonitorLayout().current_monitor.window_layout

def setup(icon):
    icon.visible = True

def exit_app():
    keyboard.clear_all_hotkeys()
    icon.stop()

def toggle_start_with_os():
    if autorun.exists(APP_NAME):
        autorun.remove(APP_NAME)
        logger.info("Start at boot disabled.")
    else:
        autorun.add(APP_NAME, sys.executable)
        logger.info("Start at boot enabled.")

def open_settings():
    os.startfile(CONF_FILE_NAME) # TODO: This is windows specific...      

try:
    # check if TydiWin is already running
    for p in psutil.process_iter():
        if p.name() == "TydiWin.exe" and os.getpid() != p.pid:
            raise MultipleInstancesNotAllowed('Multiple instances of TydiWin is not allowed.')

    # add autorun entry
    autorun.add(APP_NAME, sys.executable)

    # load conf. file to install hotkeys
    load_configuration_module()

    # add system tray icon
    # NOTE: Do not move this import to top, hotkeys will not work somehow!!!!
    import pystray 
    icon = pystray.Icon(APP_NAME)
    image = im = Image.open(os.path.join(EXEC_PATH, "icon.png"))
    icon.icon = image

    icon.menu = pystray.Menu(
        pystray.MenuItem('Start on boot', toggle_start_with_os, checked=lambda _: autorun.exists(APP_NAME)),
        pystray.MenuItem('Edit Settings', open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit', exit_app), 
        )
    icon.run(setup)
except Exception as e:
    logger.exception(e)
finally:
    logger.info("TydiWin closed.")