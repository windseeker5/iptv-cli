"""Headless smoke tests for the new IPTV TUI."""

import asyncio
import unittest

from new_iptv.app import IPTVApp
from new_iptv.screens.main_menu import MainMenuScreen
from new_iptv.screens.search import SearchScreen


class IPTVAppSmokeTest(unittest.IsolatedAsyncioTestCase):
    """Smoke tests that drive the app without a real terminal."""

    async def test_app_mounts_main_menu(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            screen = MainMenuScreen()
            pilot.app.push_screen(screen)
            await pilot.pause(0.1)
            self.assertEqual(pilot.app.screen_stack[-1], screen)
            menu = screen.query_one("#main-menu")
            self.assertIsNotNone(menu)

    async def test_search_screen_navigation(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            pilot.app.push_screen("search")
            await pilot.pause()
            self.assertIsInstance(pilot.app.screen, SearchScreen)
            pilot.app.pop_screen()
            await pilot.pause()
            self.assertIsInstance(pilot.app.screen, MainMenuScreen)


if __name__ == "__main__":
    unittest.main()
