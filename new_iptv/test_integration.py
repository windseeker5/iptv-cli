"""Integration test that drives the new IPTV TUI through main flows."""

import unittest

from new_iptv.app import IPTVApp
from new_iptv.screens.main_menu import MainMenuScreen
from new_iptv.screens.search import SearchScreen
from new_iptv.screens.results import ResultsScreen
from new_iptv.screens.favorites import FavoritesScreen
from new_iptv.screens.category_browser import CategoryBrowserScreen
from new_iptv.screens.container_status import ContainerStatusScreen
from new_iptv.screens.downloads_recordings import DownloadsScreen
from new_iptv.screens.youtube import YouTubeScreen


class IPTVIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_main_menu_mounts(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = MainMenuScreen()
            pilot.app.push_screen(screen)
            await pilot.pause()
            self.assertIsInstance(pilot.app.screen, MainMenuScreen)

    async def test_search_to_results(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            pilot.app.push_screen(SearchScreen())
            await pilot.pause()
            pilot.app.push_screen(ResultsScreen("cnn"))
            await pilot.pause(1)
            self.assertIsInstance(pilot.app.screen, ResultsScreen)

    async def test_category_browser_mounts(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            pilot.app.push_screen(CategoryBrowserScreen("live"))
            await pilot.pause(1)
            self.assertIsInstance(pilot.app.screen, CategoryBrowserScreen)

    async def test_container_status_mounts(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            pilot.app.push_screen(ContainerStatusScreen())
            await pilot.pause(1)
            self.assertIsInstance(pilot.app.screen, ContainerStatusScreen)

    async def test_downloads_screen_mounts(self):
        app = IPTVApp()
        async with app.run_test() as pilot:
            pilot.app.push_screen(DownloadsScreen())
            await pilot.pause(1)
            self.assertIsInstance(pilot.app.screen, DownloadsScreen)


if __name__ == "__main__":
    unittest.main()
