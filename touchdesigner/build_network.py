"""Builds the Finger Ninja network inside TouchDesigner.

Run it ONCE from TouchDesigner's Textport (Dialogs > Textport and DATs):

    exec(open(r'C:/Users/ANHAD/finger-ninja/touchdesigner/build_network.py').read())

(Change the path if you cloned the repo somewhere else.) It creates
/project1/finger_ninja, puts the game into the network as a node, then save the
project with Ctrl+S as touchdesigner/finger_ninja.toe.

Network:

  camera -> mirror -> game (Script TOP) ---------------------------+
                        |                                          v
                        +-> trail_source (Script TOP)             final -> out -> window
                              |                                    ^
                              v                                    |
                      trail (Composite add) <-> trail_feedback     |
                              |           ^         |              |
                              |           +- trail_fade (Level)    |
                              +-> trail_glow (Blur) ---------------+

  keys (Keyboard In CHOP) -> key_actions (CHOP Execute DAT): P pause, Q close window
"""
import os
import sys


def _find_fn_dir():
    """Find the touchdesigner/ folder (the one containing td_engine.py).

    exec() in the Textport doesn't reliably set __file__, so we check a few
    candidates and use the first one that really has td_engine.py in it.
    """
    candidates = []
    if 'FN_DIR' in globals():  # set by hand: FN_DIR = r'...'
        candidates.append(globals()['FN_DIR'])
    try:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    candidates.append('C:/Users/ANHAD/finger-ninja/touchdesigner')
    for folder in candidates:
        if folder and os.path.isfile(os.path.join(folder, 'td_engine.py')):
            return os.path.abspath(folder).replace('\\', '/')
    raise RuntimeError(
        'Could not find td_engine.py. Tell the script where the touchdesigner folder is, e.g.\n'
        "  FN_DIR = r'D:/code/finger-ninja/touchdesigner'\n"
        "  exec(open(FN_DIR + '/build_network.py').read())\n"
        'Checked: {}'.format(candidates))


FN_DIR = _find_fn_dir()
print('Finger Ninja: building from', FN_DIR)
print('  TouchDesigner Python', sys.version.split()[0])
try:
    import numpy, cv2
    print('  numpy', numpy.__version__, '| OpenCV', cv2.__version__)
except Exception as exc:
    print('  could not import numpy/cv2:', exc)

_warnings = []


def setpar(node, name, value):
    """Set a parameter, but only warn (don't stop) if this TD version names it differently."""
    try:
        par = getattr(node.par, name)
        if par is None:
            raise AttributeError(name)
        par.val = value
    except Exception as exc:
        _warnings.append('{}.par.{} = {!r}  ({})'.format(node.name, name, value, exc))


def wire(src, dst, index=0):
    """Connect src's output to dst's input number `index`."""
    try:
        dst.inputConnectors[index].connect(src)
    except Exception:
        src.outputConnectors[0].connect(dst)


def place(node, col, row):
    node.nodeX, node.nodeY = col * 200, -row * 150


def callbacks_dat(script_op, text):
    """Script TOPs get a docked callbacks DAT; create one if this TD version didn't."""
    dat = script_op.par.callbacks.eval()
    if dat is None:
        dat = script_op.parent().create(textDAT, script_op.name + '_callbacks')
        script_op.par.callbacks = dat.name
    dat.text = text
    return dat


HEADER = '''import sys
FN_DIR = r'{dir}'
if FN_DIR not in sys.path:
    sys.path.insert(0, FN_DIR)
import td_engine
'''.format(dir=FN_DIR)

GAME_CALLBACKS = HEADER + '''

def onSetupParameters(scriptOp):
    return

def onPulse(par):
    return

def onCook(scriptOp):
    # hand tracking + game update + drawing, all in td_engine.py
    td_engine.cook_game(scriptOp)
    return
'''

TRAIL_CALLBACKS = HEADER + '''

def onSetupParameters(scriptOp):
    return

def onPulse(par):
    return

def onCook(scriptOp):
    # draws the newest blade segment; the feedback loop turns it into a trail
    td_engine.cook_trail(scriptOp)
    return
'''

KEY_CALLBACKS = HEADER + '''

def onOffToOn(channel, sampleIndex, val, prev):
    key = channel.name.lower()[-1:]
    if key == 'p':
        td_engine.toggle_pause()
    elif key == 'q':
        op('window').par.winclose.pulse()
    return

def onWhileOn(channel, sampleIndex, val, prev):
    return

def onOnToOff(channel, sampleIndex, val, prev):
    return

def onWhileOff(channel, sampleIndex, val, prev):
    return

def onValueChange(channel, sampleIndex, val, prev):
    return
'''

# ------------------------------------------------------------------ build
home = op('/project1') or op('/')
old = home.op('finger_ninja')
if old is not None:
    old.destroy()
fn = home.create(baseCOMP, 'finger_ninja')

# camera -> mirror
camera = fn.create(videodeviceinTOP, 'camera')
place(camera, 0, 0)
mirror = fn.create(flipTOP, 'mirror')
setpar(mirror, 'flipx', True)
wire(camera, mirror)
place(mirror, 1, 0)

# game: hand tracking + game logic + drawing (Python, see td_engine.py)
game = fn.create(scriptTOP, 'game')
callbacks_dat(game, GAME_CALLBACKS)
wire(mirror, game)
place(game, 2, 0)

# blade trail: newest segment + feedback loop that fades older segments
trail_source = fn.create(scriptTOP, 'trail_source')
callbacks_dat(trail_source, TRAIL_CALLBACKS)
wire(game, trail_source)  # makes it cook right after the game each frame
place(trail_source, 2, 2)

trail_feedback = fn.create(feedbackTOP, 'trail_feedback')
wire(trail_source, trail_feedback)
place(trail_feedback, 3, 3)

trail_fade = fn.create(levelTOP, 'trail_fade')
setpar(trail_fade, 'brightness1', 0.72)  # each frame the old trail keeps 72% of its brightness
wire(trail_feedback, trail_fade)
place(trail_fade, 4, 3)

trail = fn.create(compositeTOP, 'trail')
setpar(trail, 'operand', 'add')  # new segment + faded old trail
wire(trail_source, trail, 0)
wire(trail_fade, trail, 1)
place(trail, 4, 2)
setpar(trail_feedback, 'top', trail.name)  # close the loop: next frame reads this

trail_glow = fn.create(blurTOP, 'trail_glow')
setpar(trail_glow, 'size', 12)
wire(trail, trail_glow)
place(trail_glow, 5, 2)

# final image = game + sharp trail + soft glow, added together
final = fn.create(compositeTOP, 'final')
setpar(final, 'operand', 'add')
wire(game, final, 0)
wire(trail, final, 1)
wire(trail_glow, final, 2)
place(final, 6, 0)

out = fn.create(nullTOP, 'out')
wire(final, out)
place(out, 7, 0)

# keyboard: P = pause, Q = close the game window
keys = fn.create(keyboardinCHOP, 'keys')
setpar(keys, 'keys', 'p q')
place(keys, 0, 5)
key_actions = fn.create(chopexecuteDAT, 'key_actions')
key_actions.text = KEY_CALLBACKS
setpar(key_actions, 'chop', keys.name)
setpar(key_actions, 'offtoon', True)
setpar(key_actions, 'valuechange', False)
place(key_actions, 1, 5)

# run the whole project at 30 frames per second, the webcam's rate
try:
    project.cookRate = 30
    print('Finger Ninja: project frame rate set to 30 FPS')
except Exception as exc:
    _warnings.append('could not set the project frame rate to 30: {}'.format(exc))
old_tick = fn.op('tick')
if old_tick is not None:
    old_tick.destroy()

# output window
window = fn.create(windowCOMP, 'window')
setpar(window, 'winop', out.path)
setpar(window, 'winw', 1280)
setpar(window, 'winh', 720)
place(window, 8, 0)

readme = fn.create(textDAT, 'README')
readme.text = globals().get('__doc__') or 'Finger Ninja - see touchdesigner/README.md'
place(readme, 0, 2)

# Put the game into the /project1 network as a node: 'finger_ninja_view'
# (a Select TOP that shows finger_ninja/out). It takes the place of the
# starter project's 'moviefilein1': same spot, and whatever moviefilein1 fed
# into now gets the game image instead. moviefilein1 is moved aside, not deleted.
def _output_targets(node):
    """(operator, input index) pairs that node's output is wired into."""
    try:
        return [(c.owner, c.index) for c in node.outputConnectors[0].connections]
    except Exception:
        return []


old_view = home.op('finger_ninja_view')
starter = home.op('moviefilein1')
old_targets = _output_targets(old_view) if old_view is not None else []
starter_targets = _output_targets(starter) if starter is not None else []
if old_view is not None and old_targets:
    # rebuilding after the swap: keep the old view's spot and wiring
    spot, targets = (old_view.nodeX, old_view.nodeY), old_targets
elif starter is not None and (starter_targets or old_view is None):
    # moviefilein1 still wired in (or first build): take its spot and wiring
    spot, targets = (starter.nodeX, starter.nodeY), starter_targets
    starter.nodeX -= 250  # move it aside
elif old_view is not None:
    spot, targets = (old_view.nodeX, old_view.nodeY), []
else:
    spot, targets = (fn.nodeX + 200, fn.nodeY), []
if old_view is not None:
    old_view.destroy()

view = home.create(selectTOP, 'finger_ninja_view')
setpar(view, 'top', fn.name + '/out')
view.nodeX, view.nodeY = spot
try:
    view.viewer = True  # live image in the node tile
except Exception:
    pass
for target, index in targets:
    try:
        target.inputConnectors[index].connect(view)
        print('Finger Ninja: connected the game to', target.name)
    except Exception as exc:
        _warnings.append('could not connect game to {}: {}'.format(target.name, exc))

# Only in the node tile, not as the network background: the Display flag
# (which draws a TOP behind the nodes) stays off.
view.display = False
out.display = False

# Python keeps imported modules in memory, so after the .py files change a
# rebuild must reload td_engine (and the game code) to pick up the new code.
import importlib
for name in ('entities', 'blade', 'game', 'hand_tracker', 'sounds', 'td_engine'):
    if name in sys.modules:
        importlib.reload(sys.modules[name])

print('Finger Ninja: built', fn.path)
print("The game is the 'finger_ninja_view' node in", home.path)
print("For a separate window, go into finger_ninja, select the 'window' node and press its 'Open' button.")
if _warnings:
    print('Some parameters could not be set (copy these lines to Claude):')
    for w in _warnings:
        print('   ', w)
else:
    print('All parameters set. Save the project with Ctrl+S.')
