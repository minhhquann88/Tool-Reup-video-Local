from types import SimpleNamespace
from unittest.mock import Mock

import main


class _BoolVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _settings(voice_on, allow_no_name):
    return SimpleNamespace(
        _voiceai_var=_BoolVar(voice_on),
        _allow_no_product_name_var=_BoolVar(allow_no_name),
        _allow_no_product_name_switch=Mock(),
        _voiceai_box=Mock(),
        _voiceai_anchor=Mock(),
        _btn_pick_audio=Mock(),
        _btn_clear_audio=Mock(),
        _mute_chk=Mock(),
    )


def test_enabling_voice_ai_clears_and_locks_render_without_product_name():
    app = _settings(voice_on=True, allow_no_name=True)

    main.App._toggle_voiceai(app)

    assert not app._allow_no_product_name_var.get()
    app._allow_no_product_name_switch.configure.assert_called_once_with(state="disabled")
    app._voiceai_box.pack.assert_called_once_with(fill="x", before=app._voiceai_anchor)


def test_disabling_voice_ai_turns_render_without_product_name_back_on():
    app = _settings(voice_on=True, allow_no_name=True)
    main.App._toggle_voiceai(app)
    app._allow_no_product_name_switch.reset_mock()

    app._voiceai_var.set(False)
    main.App._toggle_voiceai(app)

    assert app._allow_no_product_name_var.get()
    app._allow_no_product_name_switch.configure.assert_called_once_with(state="normal")
    app._voiceai_box.pack_forget.assert_called_once_with()
