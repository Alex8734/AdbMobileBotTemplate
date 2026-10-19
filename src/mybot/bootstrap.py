"""Meldet Pages, Flows, Watcher und Actions dieses Projekts beim Framework an."""

import mybot.actions
import mybot.flows
import mybot.pages
import mybot.watchers
from adbbot.bot.registry import Registries


def register(reg: Registries) -> None:
    reg.discover(mybot.pages)
    reg.discover(mybot.flows)
    reg.discover(mybot.watchers)
    reg.discover(mybot.actions)
