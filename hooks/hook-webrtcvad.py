"""
Custom PyInstaller hook for webrtcvad.
Accommodates webrtcvad-wheels distribution package metadata.
"""

from PyInstaller.utils.hooks import copy_metadata

try:
    datas = copy_metadata('webrtcvad')
except Exception:
    try:
        datas = copy_metadata('webrtcvad-wheels')
    except Exception:
        datas = []

