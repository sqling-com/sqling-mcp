"""What an object inherits, and what happens when nothing says.

An object subclassed from an object library sets almost nothing itself. Its
own row is honest -- that is what the file holds -- and useless on its own:
an alert whose title, style and buttons all come from a library reads as an
alert with nothing on it. These tests are about the half the loader adds and
the tools have to surface.

The fixture is built the way the Northwind one is, by formsrt's loader over
XML written here, so the server still depends on nothing but a file.
"""
import os
import shutil
import subprocess
import sys

import pytest

from sqling_mcp import tools
from sqling_mcp.db import Catalog, CatalogError

HERE = os.path.dirname(os.path.abspath(__file__))
FORMS = os.path.expanduser("~/formsrt")
BUILT = os.path.join(HERE, "subclassed.sqlite")

LIBRARY = """<?xml version="1.0" encoding="UTF-8"?>
<Module version="122010300" xmlns="http://xmlns.oracle.com/Forms">
  <ObjectLibrary Name="STD_OBJECTS" Language="US">
    <ObjectLibraryTab Name="STANDARD" Label="Standard">
      <Alert Name="STD_NOTE" Title="Clinic" AlertMessage="Message"
             AlertStyle="Note" Button1Label="OK"/>
      <PropertyClass Name="STD_DATE" FormatMask="DD-MON-YYYY" Width="76"
                     FontName="Tahoma"/>
    </ObjectLibraryTab>
  </ObjectLibrary>
</Module>
"""

FORM = """<?xml version="1.0" encoding="UTF-8"?>
<Module version="90200">
  <FormModule Name="CLINIC_PATIENTS" Title="Patients">
    <Alert Name="STD_NOTE" ParentModule="STD_OBJECTS" ParentName="STD_NOTE"
           ParentFilename="std_objects.olb" ParentType="1"/>
    <Block Name="PATIENTS" QueryDataSourceName="PATIENTS">
      <Item Name="BIRTH_DATE" ParentModule="STD_OBJECTS" ParentName="STD_DATE"
            ParentType="PropertyClass" Width="90"/>
      <Item Name="SURNAME" MaximumLength="40" ItemType="Text Item"/>
    </Block>
  </FormModule>
</Module>
"""

#: An alert and a trigger that come in through a subclassed object group:
#: their pointers name the group, not themselves.
GROUP_LIBRARY = """<?xml version="1.0"?><Module version="122010300">
 <FormModule Name="STD_DEF">
  <Alert Name="STOP_ALERT" Title="Stop" AlertStyle="Stop" Button1Label="OK"
         VisualAttributeName="STD_DISPLAY"/>
  <ObjectGroup Name="STANDARD_OBJECTS">
   <ObjectGroupChild Name="STOP_ALERT" Type="Alert"/>
  </ObjectGroup>
  <Trigger Name="ON-ERROR" TriggerText="message(error_text);"/>
  <Trigger Name="WHEN-NEW-FORM-INSTANCE" TriggerText="form_init.title := 'Your Title Here';"/>
 </FormModule></Module>"""

GROUP_FORM = """<?xml version="1.0"?><Module version="122010300">
 <FormModule Name="ORDERS">
  <Alert Name="STOP_ALERT" ParentModule="STD_DEF" ParentName="STANDARD_OBJECTS"
         ParentType="23" ParentFilename="std_def.fmb"/>
  <Trigger Name="ON-ERROR" ParentModule="STD_DEF" ParentName="ON-ERROR" ParentType="37"
           ParentFilename="std_def.fmb" TriggerText="message(error_text);"/>
  <Trigger Name="WHEN-NEW-FORM-INSTANCE" ParentModule="STD_DEF" ParentName="WHEN-NEW-FORM-INSTANCE"
           ParentType="37" ParentFilename="std_def.fmb" TriggerText="form_init.title := 'Orders';"/>
  <ObjectGroup Name="STANDARD_OBJECTS" ParentModule="STD_DEF"
               ParentName="STANDARD_OBJECTS" ParentType="23" ParentFilename="std_def.fmb">
   <ObjectGroupChild Name="STOP_ALERT" Type="Alert"/>
  </ObjectGroup>
 </FormModule></Module>"""

#: The same form with no library beside it: every pointer unresolved.
ALONE = FORM


@pytest.fixture(scope="session")
def subclassed(tmp_path_factory):
    if os.path.exists(BUILT):
        return BUILT
    if not os.path.isdir(FORMS):
        pytest.skip("needs formsrt's loader to build the fixture")
    xml = tmp_path_factory.mktemp("xml")
    (xml / "std_objects.xml").write_text(LIBRARY)
    (xml / "clinic_patients.xml").write_text(FORM)
    (xml / "std_def.fmb.xml").write_text(GROUP_LIBRARY)
    (xml / "orders.fmb.xml").write_text(GROUP_FORM)
    done = subprocess.run([os.path.join(FORMS, "bin", "form-metadata"),
                           str(xml), "--sqlite", BUILT],
                          capture_output=True, text=True)
    if done.returncode:
        pytest.skip("form-metadata failed: %s" % done.stderr.strip()[-200:])
    return BUILT


@pytest.fixture(scope="session")
def sub(subclassed):
    return Catalog(subclassed)


# ------------------------------------------------------------- describe

def test_describe_says_what_is_subclassed_and_from_where(sub):
    out = tools.describe_form(sub, "CLINIC_PATIENTS")
    found = out["subclassing"]
    assert found["objects"] == 2 and found["resolved"] == 2
    assert found["from_files"] == ["std_objects.xml"]
    assert "PATIENTS.BIRTH_DATE (STD_DATE)" in found["objects_listed"]
    assert "unresolved_parents" not in found


def test_a_module_with_no_subclassing_says_nothing_about_it(cat):
    """Northwind's forms subclass nothing. An empty section every time
    would be noise in every answer about every module."""
    assert "subclassing" not in tools.describe_form(cat, "NW_ORDERS")


# ------------------------------------------------------------ effective

def test_an_object_that_sets_nothing_still_has_properties(sub):
    """The case that makes this necessary: four properties, none of them
    the form's own."""
    out = tools.effective(sub, "CLINIC_PATIENTS", "STD_NOTE")
    assert out["own"] == {}
    assert out["inherited"]["title"]["value"] == "Clinic"
    assert out["inherited"]["alert_style"]["value"] == "Note"
    assert out["effective"]["button1_label"] == "OK"
    # Named by the file the library was loaded from, not by its module name.
    assert out["subclassed_from"] == {"file": "std_objects.xml",
                                      "object": "STD_NOTE",
                                      "resolved": True}


def test_what_the_object_overrides_wins(sub):
    out = tools.effective(sub, "CLINIC_PATIENTS", "PATIENTS.BIRTH_DATE")
    assert out["own"]["width"] == 90                 # the item says 90
    assert "width" not in out["inherited"]           # so it is not inherited
    assert out["inherited"]["format_mask"]["value"] == "DD-MON-YYYY"
    assert out["inherited"]["format_mask"]["from_object"] == "STANDARD.STD_DATE"
    assert out["effective"]["width"] == 90
    assert out["effective"]["format_mask"] == "DD-MON-YYYY"


def test_an_object_that_inherits_nothing_reads_the_same_either_way(sub):
    out = tools.effective(sub, "CLINIC_PATIENTS", "PATIENTS.SURNAME")
    assert out["inherited"] == {}
    assert out["subclassed_from"] is None
    assert out["own"] == out["effective"]
    assert out["own"]["maximum_length"] == 40


def test_identity_is_not_a_property(sub):
    """An object's own name and its block's are what was asked for, not
    something the object sets."""
    out = tools.effective(sub, "CLINIC_PATIENTS", "PATIENTS.BIRTH_DATE")
    assert not any(k.endswith("_name") for k in out["own"])
    assert out["object"] == "BIRTH_DATE"


def test_an_unknown_object_says_so(sub):
    with pytest.raises(CatalogError) as raised:
        tools.effective(sub, "CLINIC_PATIENTS", "NO_SUCH_ITEM")
    assert "no object called" in str(raised.value)


# -------------------------------------------------- the library is missing

@pytest.fixture(scope="session")
def orphaned(tmp_path_factory):
    """The same form loaded without its library."""
    if not os.path.isdir(FORMS):
        pytest.skip("needs formsrt's loader to build the fixture")
    xml = tmp_path_factory.mktemp("alone")
    (xml / "clinic_patients.xml").write_text(ALONE)
    out = str(tmp_path_factory.mktemp("db") / "alone.sqlite")
    done = subprocess.run([os.path.join(FORMS, "bin", "form-metadata"),
                           str(xml), "--sqlite", out],
                          capture_output=True, text=True)
    if done.returncode:
        pytest.skip("form-metadata failed: %s" % done.stderr.strip()[-200:])
    return Catalog(out)


def test_an_unresolved_parent_is_said_out_loud(orphaned):
    """"Inherited from a library I cannot see" and "inherits nothing" are
    different answers, and only one of them is this module."""
    found = tools.describe_form(orphaned, "CLINIC_PATIENTS")["subclassing"]
    assert found["objects"] == 2 and found["resolved"] == 0
    # Not loaded: the pointer's own file name is all there is to go on.
    assert found["unresolved_parents"] == ["std_objects.olb"]

    out = tools.effective(orphaned, "CLINIC_PATIENTS", "STD_NOTE")
    assert out["inherited"] == {}
    assert out["subclassed_from"]["resolved"] is False
    assert "not in this database" in out["subclassed_from"]["note"]


# ------------------------------------------ a database from before all this

def test_a_database_without_the_tables_still_answers(tmp_path):
    """Databases outlive the loader that wrote them. One built before
    subclassing was resolved has no such tables, and every tool has to
    answer anyway."""
    import sqlite3
    old = tmp_path / "old.sqlite"
    conn = sqlite3.connect(str(old))
    conn.executescript("""
        CREATE TABLE form_files (form_file_pk INTEGER PRIMARY KEY,
            file_name TEXT, file_kind TEXT, module_name TEXT, module_kind TEXT);
        CREATE TABLE form_blocks (form_block_pk INTEGER PRIMARY KEY,
            form_file_fk INTEGER, form_block_name TEXT);
        CREATE TABLE form_triggers (form_trigger_pk INTEGER PRIMARY KEY,
            form_file_fk INTEGER, form_trigger_name TEXT, parent_kind TEXT,
            parent_pk INTEGER, trigger_text TEXT);
        CREATE TABLE form_items (form_item_pk INTEGER PRIMARY KEY,
            form_file_fk INTEGER, form_block_fk INTEGER, form_item_name TEXT,
            form_block_name TEXT, maximum_length INTEGER);
        INSERT INTO form_files VALUES (1, 'OLD.fmb', 'fmb', 'OLD', 'form');
        INSERT INTO form_blocks VALUES (2, 1, 'B');
        INSERT INTO form_items VALUES (3, 1, 2, 'I', 'B', 10);
    """)
    conn.commit()
    cat = Catalog(str(old))
    assert not cat.has("form_subclass_links")
    assert "subclassing" not in tools.describe_form(cat, "OLD")
    out = tools.effective(cat, "OLD", "B.I")
    assert out["own"]["maximum_length"] == 10
    assert out["inherited"] == {} and out["subclassed_from"] is None


def test_an_alert_from_an_object_group_inherits_from_its_namesake(sub):
    """Reported against a real application: alerts and triggers that came in
    through a subclassed object group showed nothing inherited, though their
    parent sets a visual attribute -- the link pointed at the group."""
    out = tools.effective(sub, "orders.fmb", "STOP_ALERT")
    assert "several" not in out
    assert out["inherited"]["visual_attribute_name"]["value"] == "STD_DISPLAY"
    assert out["effective"]["title"] == "Stop"
    assert out["subclassed_from"]["object"] == "STOP_ALERT"
    assert out["subclassed_from"]["through_object_group"] == "STANDARD_OBJECTS"


def test_a_trigger_that_keeps_its_parents_code_says_so(sub):
    """Reported against a real application: 605 subclassed triggers showed
    nothing inherited. Forms keeps a copy of a subclassed trigger's code in the
    form, so the trigger sets everything itself; compared with its parent, the
    code is the parent's, unchanged."""
    out = tools.effective(sub, "orders.fmb", "ON-ERROR")
    assert out["matches_parent"] == ["trigger_text"] and "overrides" not in out
    assert "overrides nothing" in out["note"]


def test_a_trigger_that_replaces_its_parents_code_is_an_override(sub):
    out = tools.effective(sub, "orders.fmb", "WHEN-NEW-FORM-INSTANCE")
    assert out["overrides"]["trigger_text"] == {"own": "form_init.title := 'Orders';",
                                                "parent": "form_init.title := 'Your Title Here';"}
    assert "matches_parent" not in out


def test_an_object_group_says_what_it_brings(sub):
    out = tools.effective(sub, "orders.fmb", "STANDARD_OBJECTS")
    assert "has no properties of its own" in out["note"]
