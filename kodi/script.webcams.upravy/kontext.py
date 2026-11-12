# kontextové menu kanálu v TV: ⭐ oblíbená ano / ne
import sys

import xbmc

from resources.lib import api

try:
    li = sys.listitem
    cid = api.cid_z(li.getArt("icon"), li.getArt("thumb"), xbmc.getInfoLabel("ListItem.Icon"), li.getPath())
    nazev = xbmc.getInfoLabel("ListItem.ChannelName") or li.getLabel()
    api.prepnout_oblibenou(cid, nazev)
except api.Chyba as e:
    api.oznam(str(e), True)
