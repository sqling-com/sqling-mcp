"""A menu's code: its items' commands and its startup code.

Menus are where most of an application's CALL_FORM and OPEN_FORM calls
live, and none of it was searchable -- search and read_code read triggers
and program units only. Northwind has no menu module, so this builds one,
the way the subclassing fixture is built: XML written here, loaded by
formsrt's loader, so the server still depends on nothing but a file.
"""
import os
import subprocess
import sys

import pytest

from sqling_mcp import tools
from sqling_mcp.db import Catalog, CatalogError

HERE = os.path.dirname(os.path.abspath(__file__))
FORMS = os.path.expanduser("~/formsrt")
BUILT = os.path.join(HERE, "menu.sqlite")

MENU = """<?xml version="1.0" encoding="UTF-8"?>
<Module version="122010300" xmlns="http://xmlns.oracle.com/Forms">
  <MenuModule Name="APP_MENU" MainMenu="MAIN_MENU"
      StartupCode="DEFAULT_VALUE('N', 'GLOBAL.ADMIN');&#10;CALL_FORM('ORDERS');">
    <Menu Name="MAIN_MENU">
      <MenuItem Name="ORDERS" Label="Orders" CommandType="PL/SQL"
                MenuItemCode="CALL_FORM('ORDERS');"/>
      <MenuItem Name="CUSTOMERS" Label="Customers" CommandType="PL/SQL"
                MenuItemCode="-- pick a customer first&#10;OPEN_FORM('CUSTOMERS');"/>
      <MenuItem Name="HELP" Label="Help" CommandType="PL/SQL"
                MenuItemCode="SHOW_HELP_BOOK;"/>
      <MenuItem Name="SEP1" MenuItemType="Separator"/>
    </Menu>
    <Menu Name="ADMIN_MENU">
      <MenuItem Name="HELP" Label="Help" CommandType="PL/SQL"
                MenuItemCode="CALL_FORM('ADMIN_HELP');"/>
    </Menu>
  </MenuModule>
</Module>
"""


@pytest.fixture(scope="module")
def menu(tmp_path_factory):
    if not os.path.exists(BUILT):
        if not os.path.isdir(FORMS):
            pytest.skip("needs formsrt's loader to build the fixture")
        work = tmp_path_factory.mktemp("menu")
        (work / "app_menu.mmb.xml").write_text(MENU)
        done = subprocess.run([sys.executable, os.path.join(FORMS, "bin", "form-metadata"),
                               str(work), "--sqlite", BUILT],
                              capture_output=True, text=True)
        if done.returncode:
            pytest.skip("form-metadata failed: %s" % done.stderr.strip()[-200:])
    return Catalog(BUILT)


def test_search_reads_menu_items_and_startup_code(menu):
    out = tools.search(menu, "CALL_FORM")
    found = {(h["kind"], h["object"]) for h in out["hits"]}
    assert ("menu item", "MAIN_MENU.ORDERS") in found
    assert ("menu item", "ADMIN_MENU.HELP") in found
    assert ("startup code", "menu module") in found
    assert all(h["file"] == "app_menu.mmb" for h in out["hits"])


def test_search_finds_the_line_inside_a_menu_command(menu):
    hit = tools.search(menu, "OPEN_FORM")["hits"][0]
    assert hit["object"] == "MAIN_MENU.CUSTOMERS" and hit["line"] == 2


def test_read_code_by_item_or_menu_dot_item(menu):
    out = tools.read_code(menu, "app_menu.mmb", "ORDERS")
    assert out["kind"] == "menu item" and out["on"] == "MAIN_MENU.ORDERS"
    assert "CALL_FORM('ORDERS')" in out["text"]
    assert tools.read_code(menu, "app_menu", "MAIN_MENU.CUSTOMERS")["on"] == "MAIN_MENU.CUSTOMERS"


def test_an_item_name_in_two_menus_asks_which(menu):
    out = tools.read_code(menu, "app_menu", "HELP")
    assert {s["on"] for s in out["several"]} == {"MAIN_MENU.HELP", "ADMIN_MENU.HELP"}
    picked = tools.read_code(menu, "app_menu", "HELP", on="ADMIN_MENU")
    assert "ADMIN_HELP" in picked["text"]


def test_read_code_gives_the_startup_code(menu):
    out = tools.read_code(menu, "app_menu", "STARTUP_CODE")
    assert out["kind"] == "startup code" and "GLOBAL.ADMIN" in out["text"]


def test_describe_form_lists_a_menus_code(menu):
    out = tools.describe_form(menu, "app_menu.mmb")
    assert out["kind"] == "menu"
    kinds = [c["kind"] for c in out["menu_code"]]
    assert kinds.count("menu item") == 4 and kinds.count("startup code") == 1
    assert out["counts"]["menu_code"] == 5        # the separator has no code


def test_list_code_across_the_menu(menu):
    out = tools.list_code(menu)
    assert out["counts"] == {"menu item": 4, "startup code": 1}
    assert all("text" not in c for c in out["code"])          # names, not source
    only = tools.list_code(menu, kind="menu items", name="HEL%")
    assert sorted(c["on"] for c in only["code"]) == ["ADMIN_MENU.HELP", "MAIN_MENU.HELP"]


# --------------------------------------------------- list_code on Northwind

def test_list_code_every_trigger_of_one_name(cat):
    out = tools.list_code(cat, kind="trigger", name="WHEN-BUTTON-PRESSED")
    assert out["found"] > 1 and out["counts"] == {"trigger": out["found"]}
    assert len({c["file"] for c in out["code"]}) > 1          # across files
    assert all(c["name"] == "WHEN-BUTTON-PRESSED" for c in out["code"])


def test_list_code_in_one_file_and_by_module_kind(cat):
    one = tools.list_code(cat, file="NW_ORDERS.fmb")
    assert one["found"] > 0 and {c["file"] for c in one["code"]} == {"NW_ORDERS.fmb"}
    assert tools.list_code(cat, module_kind="menu")["found"] == 0
    assert tools.list_code(cat, module_kind="form")["found"] >= one["found"]


def test_list_code_refuses_a_kind_it_does_not_know(cat):
    with pytest.raises(CatalogError):
        tools.list_code(cat, kind="cursor")


def test_search_takes_underscore_and_percent_literally():
    from sqling_mcp.tools import _like
    assert _like("A_B%C") == "%A\\_B\\%C%"
