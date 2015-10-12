# Copyright (c) 2009-14 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.

# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import os

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GObject

from sugar3.activity import activity
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.toolbarbox import ToolbarButton
from sugar3.graphics.menuitem import MenuItem
from sugar3.graphics.alert import NotifyAlert, Alert
from sugar3.graphics import style
from sugar3.graphics.icon import Icon
from sugar3.graphics.xocolor import XoColor
from sugar3.datastore import datastore
from sugar3 import profile

from telepathy.interfaces import CHANNEL_INTERFACE
from telepathy.interfaces import CHANNEL_INTERFACE_GROUP
from telepathy.interfaces import CHANNEL_TYPE_TEXT
from telepathy.interfaces import CONN_INTERFACE_ALIASING
from telepathy.constants import CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES
from telepathy.constants import CHANNEL_TEXT_MESSAGE_TYPE_NORMAL
from telepathy.client import Connection
from telepathy.client import Channel

from dbus.service import signal
from dbus.gobject_service import ExportedGObject
from sugar3.presence import presenceservice

from gettext import gettext as _
import logging
_logger = logging.getLogger('dimensions-activity')

from json import dump as jdump
from json import dumps as jdumps
from json import loads as jloads

from StringIO import StringIO

from toolbar_utils import (radio_factory, button_factory, separator_factory)

from constants import (DECKSIZE, PRODUCT, HASH, ROMAN, WORD, CHINESE, MAYAN,
                       INCAN, DOTS, STAR, DICE, LINES)

from game import Game

MODE = 'pattern'

help_palettes = {}

BEGINNER = 0
INTERMEDIATE = 1
EXPERT = 2
LEVEL_LABELS = [_('beginner'), _('intermediate'), _('expert')]
LEVEL_DECKSIZE = [DECKSIZE / 3, DECKSIZE, DECKSIZE / 9]

NUMBER_O_BUTTONS = {}
NUMBER_C_BUTTONS = {}
LEVEL_BUTTONS = {}

SERVICE = 'org.sugarlabs.Dimensions'
IFACE = SERVICE
PATH = '/org/augarlabs/Dimensions'

PROMPT_DICT = {'pattern': _('New pattern game'),
               'number': _('New number game'),
               'word': _('New word game'),
               'custom': _('Import custom cards')}

ROBOT_TIMER_VALUES = [15, 30, 60, 120, 300]
ROBOT_TIMER_LABELS = {15: _('15 seconds'), 30: _('30 seconds'),
                      60: _('1 minute'), 120: _('2 minutes'),
                      300: _('5 minutes')}
ROBOT_TIMER_DEFAULT = 15


class Dimensions(activity.Activity):

    ''' Dimension matching game '''

    def __init__(self, handle):
        ''' Initialize the Sugar activity '''
        super(Dimensions, self).__init__(handle)
        self.ready_to_play = False
        self._prompt = ''
        self._read_journal_data()
        self._sep = []
        self._setup_toolbars()
        self._setup_canvas()

        if self.shared_activity:
            # We're joining the activity
            self.connect("joined", self._joined_cb)

            if self.get_shared():
                self._joined_cb(self)

            else:
                self._alert(_('Please wait'), _('Starting connection...'))
        else:
            # we are creating the activity
            if not self.metadata or self.metadata.get(
                    'share-scope', activity.SCOPE_PRIVATE) == \
                    activity.SCOPE_PRIVATE:
                self._alert(_('Off-line...'),
                    _('Please share or invite someone or play by yourself.'))
            else:
                self._alert(_('On-line...'),
                            _('Please wait for the connection...'))
            self.connect('shared', self._shared_cb)

        pservice = presenceservice.get_instance()
        self.owner = pservice.get_owner()
        # self._setup_presence_service()

        if not hasattr(self, '_saved_state'):
            self._saved_state = None
            self.vmw.new_game(show_selector=True)
        else:
            self.vmw.new_game(saved_state=self._saved_state,
                              deck_index=self._deck_index)
        self.ready_to_play = True

        if self._editing_word_list:
            self.vmw.editing_word_list = True
            self.vmw.edit_word_list()
        elif self._editing_custom_cards:
            self.vmw.editing_custom_cards = True
            self.vmw.edit_custom_card()

        Gdk.Screen.get_default().connect('size-changed', self._configure_cb)
        self._configure_cb(None)

    def _alert(self, title, text=None):
        xocolors = XoColor(profile.get_color().to_string())
        share_icon = Icon(icon_name='zoom-neighborhood',
                          xo_color=xocolors)

        alert = NotifyAlert(timeout=5)
        alert.props.title = title
        alert.props.msg = text
        alert.props.icon = share_icon
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()
        self._has_alert = True
        # self._fixed_resize_cb()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self._has_alert = False
        # self._fixed_resize_cb()

    def _select_game_cb(self, button, card_type):
        ''' Choose which game we are playing. '''
        if self.vmw.joiner():  # joiner cannot change level
            return
        # self.vmw.card_type = card_type
        self._prompt = PROMPT_DICT[card_type]
        self._load_new_game(card_type)

    def _load_new_game(self, card_type=None, show_selector=True):
        if not self.ready_to_play:
            return
        # self._notify_new_game(self._prompt)
        GObject.idle_add(self._new_game, card_type, show_selector)

    def _new_game(self, card_type, show_selector=True):
        if card_type == 'custom' and self.vmw.custom_paths[0] is None:
            self.image_import_cb()
        else:
            self.tools_toolbar_button.set_expanded(False)
            self.vmw.new_game(show_selector=show_selector)

    def _robot_cb(self, button=None):
        ''' Toggle robot assist on/off '''
        if self.vmw.robot:
            self.vmw.robot = False
            self.robot_button.set_tooltip(_('Play with the computer.'))
            self.robot_button.set_icon_name('robot-off')
        elif not self.vmw.editing_word_list:
            self.vmw.robot = True
            self.robot_button.set_tooltip(
                _('Stop playing with the computer.'))
            self.robot_button.set_icon_name('robot-on')

    def _level_cb(self, button, level):
        ''' Cycle between levels '''
        if self.vmw.joiner():  # joiner cannot change level
            return
        self.vmw.level = level
        # self.level_label.set_text(self.calc_level_label(self.vmw.low_score,
        #                                                 self.vmw.level))
        self._load_new_game(show_selector=False)

    def calc_level_label(self, low_score, play_level):
        ''' Show the score. '''
        if low_score[play_level] == -1:
            return LEVEL_LABELS[play_level]
        else:
            return '%s (%d:%02d)' % \
                (LEVEL_LABELS[play_level],
                 int(low_score[play_level] / 60),
                 int(low_score[play_level] % 60))

    def image_import_cb(self, button=None):
        ''' Import custom cards from the Journal '''
        self.vmw.editing_custom_cards = True
        self.vmw.editing_word_list = False
        self.vmw.edit_custom_card()
        if self.vmw.robot:
            self._robot_cb(button)

    def _number_card_O_cb(self, button, numberO):
        ''' Choose between O-card list for numbers game. '''
        if self.vmw.joiner():  # joiner cannot change decks
            return
        self.vmw.numberO = numberO
        self.vmw.card_type = 'number'
        self._load_new_game()

    def _number_card_C_cb(self, button, numberC):
        ''' Choose between C-card list for numbers game. '''
        if self.vmw.joiner():  # joiner cannot change decks
            return
        self.vmw.numberC = numberC
        self.vmw.card_type = 'number'
        self._load_new_game()

    def _edit_words_cb(self, button):
        ''' Edit the word list. '''
        self.vmw.editing_word_list = True
        self.vmw.editing_custom_cards = False
        self.vmw.edit_word_list()
        if self.vmw.robot:
            self._robot_cb(button)

    def _read_metadata(self, keyword, default_value, json=False):
        ''' If the keyword is found, return stored value '''
        if keyword in self.metadata:
            data = self.metadata[keyword]
        else:
            data = default_value

        if json:
            try:
                return jloads(data)
            except:
                pass

        return data

    def _read_journal_data(self):
        ''' There may be data from a previous instance. '''
        self._play_level = int(self._read_metadata('play_level', 0))
        self._robot_time = int(self._read_metadata('robot_time',
                                                   ROBOT_TIMER_DEFAULT))
        self._card_type = self._read_metadata('cardtype', MODE)
        self._low_score = [int(self._read_metadata('low_score_beginner', -1)),
                           int(self._read_metadata('low_score_intermediate',
                                                   -1)),
                           int(self._read_metadata('low_score_expert', -1))]

        self._all_scores = self._read_metadata(
            'all_scores',
            '{"pattern": [], "word": [], "number": [], "custom": []}',
            True)
        self._numberO = int(self._read_metadata('numberO', PRODUCT))
        self._numberC = int(self._read_metadata('numberC', HASH))
        self._matches = int(self._read_metadata('matches', 0))
        self._robot_matches = int(self._read_metadata('robot_matches', 0))
        self._total_time = int(self._read_metadata('total_time', 0))
        self._deck_index = int(self._read_metadata('deck_index', 0))
        self._word_lists = [[self._read_metadata('mouse', _('mouse')),
                             self._read_metadata('cat', _('cat')),
                             self._read_metadata('dog', _('dog'))],
                            [self._read_metadata('cheese', _('cheese')),
                             self._read_metadata('apple', _('apple')),
                             self._read_metadata('bread', _('bread'))],
                            [self._read_metadata('moon', _('moon')),
                             self._read_metadata('sun', _('sun')),
                             self._read_metadata('earth', _('earth'))]]
        self._editing_word_list = bool(int(self._read_metadata(
            'editing_word_list', 0)))
        self._editing_custom_cards = bool(int(self._read_metadata(
            'editing_custom_cards', 0)))
        if self._card_type == 'custom':
            self._custom_object = self._read_metadata('custom_object', None)
            if self._custom_object is None:
                self._card_type = MODE
        self._custom_jobject = []
        for i in range(9):
            self._custom_jobject.append(self._read_metadata(
                'custom_' + str(i), None))

    def _write_scores_to_clipboard(self, button=None):
        ''' SimpleGraph will plot the cululative results '''
        jscores = ''
        c = 0
        for key in self.vmw.all_scores.keys():
            for data in self.vmw.all_scores[key]:
                jscores += '%s: %s\n' % (str(c + 1), data[1])
                c += 1
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(jscores, -1)

    def _setup_toolbars(self):
        ''' Setup the toolbars.. '''

        tools_toolbar = Gtk.Toolbar()
        numbers_toolbar = Gtk.Toolbar()
        toolbox = ToolbarBox()

        self.activity_toolbar_button = ActivityToolbarButton(self)

        toolbox.toolbar.insert(self.activity_toolbar_button, 0)
        self.activity_toolbar_button.show()

        self.numbers_toolbar_button = ToolbarButton(
            page=numbers_toolbar,
            icon_name='number-tools')
        if MODE == 'number':
            numbers_toolbar.show()
            toolbox.toolbar.insert(self.numbers_toolbar_button, -1)
            self.numbers_toolbar_button.show()

        self.tools_toolbar_button = ToolbarButton(
            page=tools_toolbar,
            icon_name='view-source')

        self.button_pattern = button_factory(
            'new-pattern-game', toolbox.toolbar, self._select_game_cb,
            cb_arg='pattern', tooltip=_('New game'))

        self._set_extras(toolbox.toolbar)

        self._sep.append(separator_factory(toolbox.toolbar, True, False))

        stop_button = StopButton(self)
        stop_button.props.accelerator = '<Ctrl>q'
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()

        button_factory('score-copy', self.activity_toolbar_button,
                       self._write_scores_to_clipboard,
                       tooltip=_('Export scores to clipboard'))

        self.set_toolbar_box(toolbox)
        toolbox.show()

        if MODE == 'word':
            self.words_tool_button = button_factory(
                'word-tools', tools_toolbar, self._edit_words_cb,
                tooltip=_('Edit word lists.'))

        self.import_button = button_factory(
            'image-tools', tools_toolbar, self.image_import_cb,
            tooltip=_('Import custom cards'))

        self.button_custom = button_factory(
            'new-custom-game', tools_toolbar, self._select_game_cb,
            cb_arg='custom', tooltip=_('New custom game'))
        self.button_custom.set_sensitive(False)

        if MODE == 'number':
            self._setup_number_buttons(numbers_toolbar)

    def _setup_number_buttons(self, numbers_toolbar):
        self.product_button = radio_factory(
            'product',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=PRODUCT,
            tooltip=_('product'),
            group=None)
        NUMBER_O_BUTTONS[PRODUCT] = self.product_button
        self.roman_button = radio_factory(
            'roman',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=ROMAN,
            tooltip=_('Roman numerals'),
            group=self.product_button)
        NUMBER_O_BUTTONS[ROMAN] = self.roman_button
        self.word_button = radio_factory(
            'word',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=WORD,
            tooltip=_('word'),
            group=self.product_button)
        NUMBER_O_BUTTONS[WORD] = self.word_button
        self.chinese_button = radio_factory(
            'chinese',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=CHINESE,
            tooltip=_('Chinese'),
            group=self.product_button)
        NUMBER_O_BUTTONS[CHINESE] = self.chinese_button
        self.mayan_button = radio_factory(
            'mayan',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=MAYAN,
            tooltip=_('Mayan'),
            group=self.product_button)
        NUMBER_O_BUTTONS[MAYAN] = self.mayan_button
        self.incan_button = radio_factory(
            'incan',
            numbers_toolbar,
            self._number_card_O_cb,
            cb_arg=INCAN,
            tooltip=_('Quipu'),
            group=self.product_button)
        NUMBER_O_BUTTONS[INCAN] = self.incan_button

        separator_factory(numbers_toolbar, False, True)

        self.hash_button = radio_factory(
            'hash',
            numbers_toolbar,
            self._number_card_C_cb,
            cb_arg=HASH,
            tooltip=_('hash marks'),
            group=None)
        NUMBER_C_BUTTONS[HASH] = self.hash_button
        self.dots_button = radio_factory(
            'dots',
            numbers_toolbar,
            self._number_card_C_cb,
            cb_arg=DOTS,
            tooltip=_('dots in a circle'),
            group=self.hash_button)
        NUMBER_C_BUTTONS[DOTS] = self.dots_button
        self.star_button = radio_factory(
            'star',
            numbers_toolbar,
            self._number_card_C_cb,
            cb_arg=STAR,
            tooltip=_('points on a star'),
            group=self.hash_button)
        NUMBER_C_BUTTONS[STAR] = self.star_button
        self.dice_button = radio_factory(
            'dice',
            numbers_toolbar,
            self._number_card_C_cb,
            cb_arg=DICE,
            tooltip=_('dice'),
            group=self.hash_button)
        NUMBER_C_BUTTONS[DICE] = self.dice_button
        self.lines_button = radio_factory(
            'lines',
            numbers_toolbar,
            self._number_card_C_cb,
            cb_arg=LINES,
            tooltip=_('dots in a line'),
            group=self.hash_button)
        NUMBER_C_BUTTONS[LINES] = self.lines_button

    def _configure_cb(self, event):
        self._vbox.set_size_request(Gdk.Screen.width(), Gdk.Screen.height())
        self._vbox.show()

        if Gdk.Screen.width() < Gdk.Screen.height():
            for sep in self._sep:
                sep.hide()
        else:
            for sep in self._sep:
                sep.show()

    def _robot_selection_cb(self, widget):
        if self._robot_palette:
            if not self._robot_palette.is_up():
                self._robot_palette.popup(immediate=True,
                                          state=self._robot_palette.SECONDARY)
            else:
                self._robot_palette.popdown(immediate=True)
            return

    def _setup_robot_palette(self):
        self._robot_palette = self._robot_time_button.get_palette()

        self._robot_timer_menu = {}
        for seconds in ROBOT_TIMER_VALUES:
            self._robot_timer_menu[seconds] = {
                'menu_item': MenuItem(),
                'label': ROBOT_TIMER_LABELS[seconds],
                'icon': image_from_svg_file(
                    'timer-%d.svg' % (seconds)),
                'icon-selected': image_from_svg_file(
                    'timer-%d-selected.svg' % (seconds))
            }

            self._robot_timer_menu[seconds]['menu_item'].set_label(
                self._robot_timer_menu[seconds]['label'])
            if seconds == ROBOT_TIMER_DEFAULT:
                self._robot_timer_menu[seconds]['menu_item'].set_image(
                    self._robot_timer_menu[seconds]['icon-selected'])
            else:
                self._robot_timer_menu[seconds]['menu_item'].set_image(
                    self._robot_timer_menu[seconds]['icon'])
            self._robot_timer_menu[seconds]['menu_item'].connect(
                'activate', self._robot_selected_cb, seconds)
            self._robot_palette.menu.append(
                self._robot_timer_menu[seconds]['menu_item'])
            self._robot_timer_menu[seconds]['menu_item'].show()

    def _robot_selected_cb(self, button, seconds):
        self.vmw.robot_time = seconds
        if hasattr(self, '_robot_time_button') and \
                seconds in ROBOT_TIMER_VALUES:
            self._robot_time_button.set_icon_name('timer-%d' % seconds)

        for time in ROBOT_TIMER_VALUES:
            if time == seconds:
                self._robot_timer_menu[time]['menu_item'].set_image(
                    self._robot_timer_menu[time]['icon-selected'])
            else:
                self._robot_timer_menu[time]['menu_item'].set_image(
                    self._robot_timer_menu[time]['icon'])

    def _set_extras(self, toolbar):
        self.robot_button = button_factory(
            'robot-off', toolbar, self._robot_cb,
            tooltip=_('Play with the computer'))

        self._robot_time_button = button_factory(
            'timer-15',
            toolbar,
            self._robot_selection_cb,
            tooltip=_('robot pause time'))
        self._setup_robot_palette()

        self._sep.append(separator_factory(toolbar, False, True))

        self.beginner_button = radio_factory(
            'beginner',
            toolbar,
            self._level_cb,
            cb_arg=BEGINNER,
            tooltip=_('beginner'),
            group=None)
        LEVEL_BUTTONS[BEGINNER] = self.beginner_button
        self.intermediate_button = radio_factory(
            'intermediate',
            toolbar,
            self._level_cb,
            cb_arg=INTERMEDIATE,
            tooltip=_('intermediate'),
            group=self.beginner_button)
        LEVEL_BUTTONS[INTERMEDIATE] = self.intermediate_button
        self.expert_button = radio_factory(
            'expert',
            toolbar,
            self._level_cb,
            cb_arg=EXPERT,
            tooltip=_('expert'),
            group=self.beginner_button)
        LEVEL_BUTTONS[EXPERT] = self.expert_button

    def _fixed_resize_cb(self, widget=None, rect=None):
        ''' If a toolbar opens or closes, we need to resize the vbox
        holding out scrolling window. '''
        self._vbox.set_size_request(rect.width, rect.height)

    def _setup_canvas(self):
        ''' Create a canvas in a Gtk.Fixed '''
        self.fixed = Gtk.Fixed()
        self.fixed.connect('size-allocate', self._fixed_resize_cb)
        self.fixed.show()
        self.set_canvas(self.fixed)

        self._vbox = Gtk.VBox(False, 0)
        self._vbox.set_size_request(Gdk.Screen.width(), Gdk.Screen.height())
        self.fixed.put(self._vbox, 0, 0)
        self._vbox.show()

        self._canvas = Gtk.DrawingArea()
        self._canvas.set_size_request(int(Gdk.Screen.width()),
                                      int(Gdk.Screen.height()))
        self._canvas.show()
        self.show_all()
        self._vbox.pack_end(self._canvas, True, True, 0)
        self._vbox.show()

        self.show_all()

        self.vmw = Game(self._canvas, parent=self, card_type=MODE)
        self.vmw.level = self._play_level
        LEVEL_BUTTONS[self._play_level].set_active(True)
        self.vmw.card_type = self._card_type
        self.vmw.robot = False
        self.vmw.robot_time = self._robot_time
        self.vmw.low_score = self._low_score
        self.vmw.all_scores = self._all_scores
        self.vmw.numberO = self._numberO
        self.vmw.numberC = self._numberC
        self.vmw.matches = self._matches
        self.vmw.robot_matches = self._robot_matches
        self.vmw.total_time = self._total_time
        self.vmw.buddies = []
        self.vmw.word_lists = self._word_lists
        self.vmw.editing_word_list = self._editing_word_list
        if hasattr(self, '_custom_object') and self._custom_object is not None:
            self.vmw._find_custom_paths(datastore.get(self._custom_object))
        for i in range(9):
            if hasattr(self, '_custom_jobject') and \
               self._custom_jobject[i] is not None:
                self.vmw.custom_paths[i] = datastore.get(
                    self._custom_jobject[i])
                self.button_custom.set_sensitive(True)
        return self._canvas

    def write_file(self, file_path):
        ''' Write data to the Journal. '''
        if hasattr(self, 'vmw'):
            self.metadata['play_level'] = self.vmw.level
            self.metadata['low_score_beginner'] = int(self.vmw.low_score[0])
            self.metadata['low_score_intermediate'] = int(
                self.vmw.low_score[1])
            self.metadata['low_score_expert'] = int(self.vmw.low_score[2])
            self.metadata['all_scores'] = jdumps(self.vmw.all_scores)
            print jdumps(self.vmw.all_scores)
            self.metadata['robot_time'] = self.vmw.robot_time
            self.metadata['numberO'] = self.vmw.numberO
            self.metadata['numberC'] = self.vmw.numberC
            self.metadata['cardtype'] = self.vmw.card_type
            self.metadata['matches'] = self.vmw.matches
            self.metadata['robot_matches'] = self.vmw.robot_matches
            self.metadata['total_time'] = int(self.vmw.total_time)
            self.metadata['deck_index'] = self.vmw.deck.index
            self.metadata['mouse'] = self.vmw.word_lists[0][0]
            self.metadata['cat'] = self.vmw.word_lists[0][1]
            self.metadata['dog'] = self.vmw.word_lists[0][2]
            self.metadata['cheese'] = self.vmw.word_lists[1][0]
            self.metadata['apple'] = self.vmw.word_lists[1][1]
            self.metadata['bread'] = self.vmw.word_lists[1][2]
            self.metadata['moon'] = self.vmw.word_lists[2][0]
            self.metadata['sun'] = self.vmw.word_lists[2][1]
            self.metadata['earth'] = self.vmw.word_lists[2][2]
            self.metadata['editing_word_list'] = self.vmw.editing_word_list
            self.metadata['mime_type'] = 'application/x-visualmatch'
            f = file(file_path, 'w')
            f.write(self._dump())
            f.close()
        else:
            _logger.debug('Deferring saving to %s' % file_path)

    def _dump(self):
        ''' Dump game data to the journal.'''
        data = []
        for i in self.vmw.grid.grid:
            if i is None or self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(i.index)
        for i in self.vmw.clicked:
            if i.spr is None or self.vmw.deck.spr_to_card(i.spr) is None or \
               self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(self.vmw.deck.spr_to_card(i.spr).index)
        for i in self.vmw.deck.cards:
            if i is None or self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(i.index)
        for i in self.vmw.match_list:
            if self.vmw.deck.spr_to_card(i) is not None:
                data.append(self.vmw.deck.spr_to_card(i).index)
        for i in self.vmw.word_lists:
            for j in i:
                data.append(j)
        return self._data_dumper(data)

    def _data_dumper(self, data):
        io = StringIO()
        jdump(data, io)
        return io.getvalue()

    def read_file(self, file_path):
        ''' Read data from the Journal. '''
        f = open(file_path, 'r')
        self._load(f.read())
        f.close()

    def _load(self, data):
        ''' Load game data from the journal. '''
        saved_state = self._data_loader(data)
        if len(saved_state) > 0:
            self._saved_state = saved_state

    def _data_loader(self, data):
        json_data = jloads(data)
        return json_data

    def _notify_new_game(self, prompt):
        ''' Called from New Game button since loading a new game can
        be slooow!! '''
        alert = NotifyAlert(3)
        alert.props.title = prompt
        alert.props.msg = _('A new game is loading.')

        def _notification_alert_response_cb(alert, response_id, self):
            self.remove_alert(alert)

        alert.connect('response', _notification_alert_response_cb, self)
        self.add_alert(alert)
        alert.show()

    def _new_help_box(self, name, button=None):
        help_box = Gtk.VBox()
        help_box.set_homogeneous(False)
        help_palettes[name] = help_box
        if button is not None:
            help_buttons[name] = button
        help_windows[name] = Gtk.ScrolledWindow()
        help_windows[name].set_size_request(
            int(Gdk.Screen.width() / 3),
            Gdk.Screen.height() - style.GRID_CELL_SIZE * 3)
        help_windows[name].set_policy(Gtk.PolicyType.NEVER,
                                      Gtk.PolicyType.AUTOMATIC)
        help_windows[name].add_with_viewport(help_palettes[name])
        help_palettes[name].show()
        return help_box

    def _setup_toolbar_help(self):
        ''' Set up a help palette for the main toolbars '''
        help_box = self._new_help_box('main-toolbar')
        add_section(help_box, _('Dimensions'), icon='activity-dimensions')
        add_paragraph(help_box, _('Tools'), icon='view-source')
        if MODE == 'pattern':
            add_paragraph(help_box, _('Game'), icon='new-pattern-game')
        elif MODE == 'number':
            add_paragraph(help_box, PROMPT_DICT['number'],
                          icon='new-number-game')
            add_paragraph(help_box, _('Numbers'), icon='number-tools')
        elif MODE == 'word':
            add_paragraph(help_box, PROMPT_DICT['word'], icon='new-word-game')
        add_paragraph(help_box, _('Play with the computer'), icon='robot-off')
        add_paragraph(help_box, _('robot pause time'), icon='timer-60')
        add_paragraph(help_box, _('beginner'), icon='beginner')
        add_paragraph(help_box, _('intermediate'), icon='intermediate')
        add_paragraph(help_box, _('expert'), icon='expert')

        add_section(help_box, _('Dimensions'), icon='activity-dimensions')
        add_paragraph(help_box, _('Export scores to clipboard'),
                      icon='score-copy')

        add_section(help_box, _('Tools'), icon='view-source')
        add_section(help_box, _('Import image cards'), icon='image-tools')
        add_paragraph(help_box, PROMPT_DICT['custom'], icon='new-custom-game')
        if MODE == 'word':
            add_section(help_box, _('Edit word lists.'), icon='word-tools')

        if MODE == 'number':
            add_section(help_box, _('Numbers'), icon='number-tools')
            add_paragraph(help_box, _('product'), icon='product')
            add_paragraph(help_box, _('Roman numerals'), icon='roman')
            add_paragraph(help_box, _('word'), icon='word')
            add_paragraph(help_box, _('Chinese'), icon='chinese')
            add_paragraph(help_box, _('Mayan'), icon='mayan')
            add_paragraph(help_box, _('Quipu'), icon='incan')
            add_paragraph(help_box, _('hash marks'), icon='hash')
            add_paragraph(help_box, _('dots in a circle'), icon='dots')
            add_paragraph(help_box, _('points on a star'), icon='star')
            add_paragraph(help_box, _('dice'), icon='dice')
            add_paragraph(help_box, _('dots in a line'), icon='lines')

    ###
    def _shared_cb(self, sender):
        self._setup_presence_service()

        self.initiating = True
        self.waiting_for_deck = False
        _logger.debug('I am sharing...')

    def _joined_cb(self, sender):
        '''Joined a shared activity.'''
        if not self.shared_activity:
            return
        logger.debug('Joined a shared chat')
        for buddy in self.shared_activity.get_joined_buddies():
            self._buddy_already_exists(buddy)
        self._setup_presence_service()

        self.initiating = False
        _logger.debug('I joined a shared activity.')

        self.waiting_for_deck = True

        self.button_pattern.set_sensitive(False)
        self.robot_button.set_sensitive(False)
        self._robot_time_button.set_sensitive(False)
        self.beginner_button.set_sensitive(False)
        self.intermediate_button.set_sensitive(False)
        self.expert_button.set_sensitive(False)

    def _setup_presence_service(self):
        self.text_channel = TextChannelWrapper(
            self.shared_activity.telepathy_text_chan,
            self.shared_activity.telepathy_conn)
        self.text_channel.set_received_callback(self._received_cb)

        ## owner = self.pservice.get_owner()
        ## self.owner = owner
        self.vmw.buddies.append(self.owner)
        self._share = ''

        self.shared_activity.connect('buddy-joined', self._buddy_joined_cb)
        self.shared_activity.connect('buddy-left', self._buddy_left_cb)

    def _received_cb(self, buddy, text):
        '''Show message that was received.'''
        if buddy:
            if type(buddy) is dict:
                nick = buddy['nick']
            else:
                nick = buddy.props.nick
        else:
            nick = '???'
        logger.debug('Received message from %s: %s', nick, text)
        self.event_received_cb(text)

    def _buddy_joined_cb(self, sender, buddy):
        '''Show a buddy who joined'''
        if buddy == self.owner:
            return

    def _buddy_left_cb(self, sender, buddy):
        '''Show a buddy who joined'''
        if buddy == self.owner:
            return

    def _buddy_already_exists(self, buddy):
        '''Show a buddy already in the chat.'''
        if buddy == self.owner:
            return

    def can_close(self):
        '''Perform cleanup before closing.'''
        return True
    
    def event_received_cb(self, text):
        ''' Data is passed as tuples: cmd:text '''
        if text[0] == 'B':
            e, card_index = text.split(':')
            self.vmw.add_to_clicked(
                self.vmw.deck.index_to_card(int(card_index)).spr)
        elif text[0] == 'r':
            self.vmw.clean_up_match()
        elif text[0] == 'R':
            self.vmw.clean_up_no_match(None)
        elif text[0] == 'S':
            e, card_index = text.split(':')
            i = int(card_index)
            self.vmw.process_click(self.vmw.clicked[i].spr)
            self.vmw.process_selection(self.vmw.clicked[i].spr)
        elif text[0] == 'j':
            if self.initiating:  # Only the sharer 'shares'.
                self._send_event('P:' + str(self.vmw.level))
                self._send_event('X:' + str(self.vmw.deck.index))
                self._send_event('M:' + str(self.vmw.matches))
                self._send_event('C:' + self.vmw.card_type)
                self._send_event('D:' + str(self._dump()))
        elif text[0] == 'J':  # Force a request for current state.
            self._send_event('j')
            self.waiting_for_deck = True
        elif text[0] == 'C':
            e, text = text.split(':')
            self.vmw.card_type = text
        elif text[0] == 'P':
            e, text = text.split(':')
            self.vmw.level = int(text)
            # self.level_label.set_text(self.calc_level_label(
            #     self.vmw.low_score, self.vmw.level))
            LEVEL_BUTTONS[self.vmw.level].set_active(True)
        elif text[0] == 'X':
            e, text = text.split(':')
            self.vmw.deck.index = int(text)
        elif text[0] == 'M':
            e, text = text.split(':')
            self.vmw.matches = int(text)
        elif text[0] == 'D':
            if self.waiting_for_deck:
                e, text = text.split(':')
                self._load(text)
                self.waiting_for_deck = False
            self.vmw.new_game(self._saved_state, self.vmw.deck.index)

    def _send_event(self, entry):
        ''' Send event through the tube. '''
        if self.text_channel:
            self.text_channel.post(entry)


def image_from_svg_file(filename):
    path = os.path.join(activity.get_bundle_path(), 'icons', filename)
    fd = open(path)
    svg = fd.read()
    fd.close()
    pl = GdkPixbuf.PixbufLoader.new_with_type('svg')
    pl.write(svg)
    pl.close()
    pixbuf = pl.get_pixbuf()
    image = Gtk.Image()
    image.set_from_pixbuf(pixbuf)
    return image


class TextChannelWrapper(object):
    '''Wrap a telepathy Text Channfel to make usage simpler.'''

    def __init__(self, text_chan, conn):
        '''Connect to the text channel'''
        self._activity_cb = None
        self._activity_close_cb = None
        self._text_chan = text_chan
        self._conn = conn
        self._logger = logging.getLogger(
            'chat-activity.TextChannelWrapper')
        self._signal_matches = []
        m = self._text_chan[CHANNEL_INTERFACE].connect_to_signal(
            'Closed', self._closed_cb)
        self._signal_matches.append(m)

    def post(self, text):
        if text is not None:
            self.send(text)

    def send(self, text):
        '''Send text over the Telepathy text channel.'''
        # XXX Implement CHANNEL_TEXT_MESSAGE_TYPE_ACTION
        logging.debug('sending %s' % text)

        text = text.replace('/', SLASH)

        if self._text_chan is not None:
            self._text_chan[CHANNEL_TYPE_TEXT].Send(
                CHANNEL_TEXT_MESSAGE_TYPE_NORMAL, text)

    def close(self):
        '''Close the text channel.'''
        self._logger.debug('Closing text channel')
        try:
            self._text_chan[CHANNEL_INTERFACE].Close()
        except Exception:
            self._logger.debug('Channel disappeared!')
            self._closed_cb()

    def _closed_cb(self):
        '''Clean up text channel.'''
        self._logger.debug('Text channel closed.')
        for match in self._signal_matches:
            match.remove()
        self._signal_matches = []
        self._text_chan = None
        if self._activity_close_cb is not None:
            self._activity_close_cb()

    def set_received_callback(self, callback):
        '''Connect the function callback to the signal.

        callback -- callback function taking buddy and text args
        '''
        if self._text_chan is None:
            return
        self._activity_cb = callback
        m = self._text_chan[CHANNEL_TYPE_TEXT].connect_to_signal(
            'Received', self._received_cb)
        self._signal_matches.append(m)

    def handle_pending_messages(self):
        '''Get pending messages and show them as received.'''
        for identity, timestamp, sender, type_, flags, text in \
            self._text_chan[
                CHANNEL_TYPE_TEXT].ListPendingMessages(False):
            self._received_cb(identity, timestamp, sender, type_, flags, text)

    def _received_cb(self, identity, timestamp, sender, type_, flags, text):
        '''Handle received text from the text channel.

        Converts sender to a Buddy.
        Calls self._activity_cb which is a callback to the activity.
        '''
        logging.debug('received_cb %r %s' % (type_, text))
        if type_ != 0:
            # Exclude any auxiliary messages
            return

        text = text.replace(SLASH, '/')

        if self._activity_cb:
            try:
                self._text_chan[CHANNEL_INTERFACE_GROUP]
            except Exception:
                # One to one XMPP chat
                nick = self._conn[
                    CONN_INTERFACE_ALIASING].RequestAliases([sender])[0]
                buddy = {'nick': nick, 'color': '#000000,#808080'}
                logging.error('Exception: recieved from sender %r buddy %r' %
                              (sender, buddy))
            else:
                # Normal sugar MUC chat
                # XXX: cache these
                buddy = self._get_buddy(sender)
                logging.error('Else: recieved from sender %r buddy %r' %
                              (sender, buddy))
            self._activity_cb(buddy, text)
            self._text_chan[
                CHANNEL_TYPE_TEXT].AcknowledgePendingMessages([identity])
        else:
            self._logger.debug('Throwing received message on the floor'
                               ' since there is no callback connected. See'
                               ' set_received_callback')

    def set_closed_callback(self, callback):
        '''Connect a callback for when the text channel is closed.

        callback -- callback function taking no args

        '''
        self._activity_close_cb = callback

    def _get_buddy(self, cs_handle):
        '''Get a Buddy from a (possibly channel-specific) handle.'''
        # XXX This will be made redundant once Presence Service
        # provides buddy resolution
        # Get the Presence Service
        pservice = presenceservice.get_instance()
        # Get the Telepathy Connection
        tp_name, tp_path = pservice.get_preferred_connection()
        conn = Connection(tp_name, tp_path)
        group = self._text_chan[CHANNEL_INTERFACE_GROUP]
        my_csh = group.GetSelfHandle()
        if my_csh == cs_handle:
            handle = conn.GetSelfHandle()
        elif group.GetGroupFlags() & \
             CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES:
            handle = group.GetHandleOwners([cs_handle])[0]
        else:
            handle = cs_handle

            # XXX: deal with failure to get the handle owner
            assert handle != 0

        return pservice.get_buddy_by_telepathy_handle(
            tp_name, tp_path, handle)
