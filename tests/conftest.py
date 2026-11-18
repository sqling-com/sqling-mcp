"""The fixture application.

Northwind, because it is the sample that ships in formsrt and can be rebuilt
by anyone: seven forms, 91 triggers, nine program units. The database is built
once here from the .fmb files if it is not already lying about -- which needs
node, formsrt and sqling's readers, so the whole suite skips politely without
them rather than pretending to pass. Delete tests/northwind.sqlite to rebuild.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

FORMS = os.path.expanduser("~/formsrt")
SAMPLE = os.path.join(FORMS, "samples", "northwind")
BUILT = os.path.join(HERE, "northwind.sqlite")


def _build(target):
    """.fmb -> tables, by formsrt's loader reading the forms directly: each
    module is recorded under its own file name, NW_ORDERS.fmb, as a database
    built on the Batch page is."""
    forms = sorted(os.path.join(SAMPLE, f) for f in os.listdir(SAMPLE)
                   if f.lower().endswith(".fmb"))
    done = subprocess.run(
        [sys.executable, os.path.join(FORMS, "bin", "form-metadata")] + forms
        + ["--sqlite", target], capture_output=True, text=True)
    if done.returncode:
        pytest.skip("form-metadata failed: %s" % done.stderr.strip()[-200:])


@pytest.fixture(scope="session")
def database():
    if os.path.exists(BUILT):
        return BUILT
    if not (os.path.isdir(SAMPLE) and shutil.which("node")):
        pytest.skip("needs formsrt's northwind sample and node to build the fixture")
    _build(BUILT)
    return BUILT


@pytest.fixture(scope="session")
def cat(database):
    from sqling_mcp.db import Catalog
    return Catalog(database)


# ------------------------------------------------------------------ reports
#
# Northwind's three reports have no program units or formulas, so one more is
# written here: a parameter, a query, a formula and a placeholder backed by
# functions, a format trigger, two report triggers, and a function nothing
# points at. Its module name (SALES_SUMMARY7) is not its file's, which is the
# normal case. A small form names three reports to run: one loaded, one
# without folder or extension, one missing.

REPORT = """<?xml version="1.0" encoding="UTF-8" ?>
<report name="SALES_SUMMARY7" DTDVersion="9.0.2.0.10"
        beforeReportTrigger="beforereport" afterParameterFormTrigger="afterpform">
  <data>
    <userParameter name="P_REGION" datatype="character" width="10" initialValue="EAST"
                   validationTrigger="p_regionvalidtrigger"
                   label="Region" defaultWidth="0" defaultHeight="0"/>
    <dataSource name="Q_SALES">
      <select><![CDATA[SELECT R.REGION, O.ORDERID, O.TOTAL
  FROM ORDERS O JOIN REGIONS R ON R.ID = O.REGION_ID
 WHERE R.REGION = :P_REGION]]></select>
      <group name="G_REGION">
        <dataItem name="REGION" datatype="vchar2" width="10">
          <dataDescriptor expression="R.REGION" descriptiveExpression="REGION" order="1" width="10"/>
        </dataItem>
        <dataItem name="TOTAL" datatype="number" width="10">
          <dataDescriptor expression="O.TOTAL" descriptiveExpression="TOTAL" order="3" width="10"/>
        </dataItem>
        <formula name="CF_TAX" source="cf_taxformula" datatype="number" width="10"/>
        <summary name="CS_REGION_TOTAL" source="TOTAL" function="sum" datatype="number"
                 width="12" defaultLabel="Region total"/>
      </group>
    </dataSource>
    <dataSource name="Q_LINES">
      <select><![CDATA[SELECT L.ORDERID, L.PRODUCT, L.QTY FROM ORDER_LINES L]]></select>
      <group name="G_LINES">
        <dataItem name="ORDERID1" datatype="number" width="10">
          <dataDescriptor expression="L.ORDERID" descriptiveExpression="ORDERID" order="1" width="10"/>
        </dataItem>
      </group>
    </dataSource>
    <link name="L_1" parentGroup="G_REGION" parentColumn="REGION" childQuery="Q_LINES"
          childColumn="ORDERID1" condition="eq" sqlClause="where"/>
    <placeholder name="CP_TITLE" source="cp_titleformula" datatype="character" width="40"/>
  </data>
  <layout>
  <section name="main">
    <body>
      <frame name="M_BODY">
        <field name="F_TOTAL" source="TOTAL">
          <advancedLayout formatTrigger="f_totalformattrigger"/>
        </field>
      </frame>
    </body>
  </section>
  </layout>
  <programUnits>
    <function name="beforereport">
      <textSource><![CDATA[function BeforeReport return boolean is
begin
  SRW.MESSAGE(1, 'Starting the sales summary');
  return (TRUE);
end;]]></textSource>
    </function>
    <function name="afterpform">
      <textSource><![CDATA[function AfterPForm return boolean is
begin
  if :P_REGION is null then
    :P_REGION := 'EAST';
  end if;
  return (TRUE);
end;]]></textSource>
    </function>
    <function name="cf_taxformula" returnType="number">
      <textSource><![CDATA[function CF_TAXFormula return Number is
begin
  return :TOTAL * 0.07;
end;]]></textSource>
    </function>
    <function name="cp_titleformula" returnType="character">
      <textSource><![CDATA[function CP_TITLEFormula return Char is
begin
  return 'Sales for ' || :P_REGION;
end;]]></textSource>
    </function>
    <function name="f_totalformattrigger">
      <textSource><![CDATA[function F_TOTALFormatTrigger return boolean is
begin
  return (:TOTAL > 0);
end;]]></textSource>
    </function>
    <function name="p_regionvalidtrigger">
      <textSource><![CDATA[function P_REGIONValidTrigger return boolean is
begin
  return (:P_REGION in ('EAST', 'WEST'));
end;]]></textSource>
    </function>
    <function name="unused_helper">
      <textSource><![CDATA[function Unused_Helper return boolean is
begin
  return (TRUE);
end;]]></textSource>
    </function>
  </programUnits>
</report>
"""

LAUNCHER = """<?xml version="1.0" encoding="UTF-8"?>
<Module version="122010300" xmlns="http://xmlns.oracle.com/Forms">
  <FormModule Name="LAUNCHER" Title="Reports">
    <Report Name="SALES" Filename="/apps/reports/monthly_sales.rdf" ReportDestinationType="Screen"/>
    <Report Name="CATALOG" Filename="NW_PRODUCT_CATALOG"/>
    <Report Name="GONE" Filename="missing_report.rdf"/>
    <Report Name="PREVIEW"/>
    <Report Name="RP2RRO"/>
    <Block Name="CTL">
      <Item Name="RUN_MORE" ItemType="Push Button">
        <Trigger Name="WHEN-BUTTON-PRESSED"
                 TriggerText="declare v_form varchar2(30) := 'ORDERS'; v varchar2(100);&#10;begin&#10;  CALL_FORM(v_form);&#10;  CALL_FORM('NW_ORDERS');&#10;  v := RUN_REPORT_OBJECT(FIND_REPORT_OBJECT('PREVIEW'));&#10;  v := RUN_REPORT_OBJECT(FIND_REPORT_OBJECT('NOPE'));&#10;  rp2rro.rp2rro_run_product(REPORTS, 'NW_PRODUCT_CATALOG', SYNCHRONOUS, RUNTIME, FILESYSTEM, NULL, NULL);&#10;  v := app_pkg.build_report_url('NW_CUSTOMERS_BY_COUNTRY', 'PDF');&#10;  insert into audit_log values (sysdate);&#10;end;"/>
      </Item>
      <Item Name="RUN_SALES" ItemType="Push Button">
        <Trigger Name="WHEN-BUTTON-PRESSED"
                 TriggerText="declare r report_object; v varchar2(100); begin r := FIND_REPORT_OBJECT('SALES'); v := RUN_REPORT_OBJECT(r); end;"/>
      </Item>
    </Block>
  </FormModule>
</Module>
"""

REPORTS_BUILT = os.path.join(HERE, "reports.sqlite")
LAUNCHER_BUILT = os.path.join(HERE, "launcher.sqlite")


def _load(folder, target):
    done = subprocess.run(
        [sys.executable, os.path.join(FORMS, "bin", "form-metadata"), str(folder),
         "--sqlite", target], capture_output=True, text=True)
    if done.returncode:
        pytest.skip("form-metadata failed: %s" % done.stderr.strip()[-200:])


@pytest.fixture(scope="session")
def report_databases(tmp_path_factory):
    """(forms database, reports database): the launcher form, and four reports."""
    if not (os.path.exists(REPORTS_BUILT) and os.path.exists(LAUNCHER_BUILT)):
        if not os.path.isdir(SAMPLE):
            pytest.skip("needs formsrt's loader and its northwind sample")
        work = tmp_path_factory.mktemp("reports")
        reps = work / "reports"
        reps.mkdir()
        (reps / "monthly_sales.rdf.xml").write_text(REPORT)
        nw = os.path.join(SAMPLE, "reports")
        for name in sorted(os.listdir(nw)):
            if name.endswith(".xml"):
                shutil.copy(os.path.join(nw, name), reps / (name[:-4] + ".rdf.xml"))
        forms = work / "forms"
        forms.mkdir()
        (forms / "launcher.fmb.xml").write_text(LAUNCHER)
        _load(reps, REPORTS_BUILT)
        _load(forms, LAUNCHER_BUILT)
    return LAUNCHER_BUILT, REPORTS_BUILT


@pytest.fixture(scope="session")
def rcat(report_databases):
    """The launcher form and the reports, opened together."""
    from sqling_mcp.db import Catalog
    forms, reports = report_databases
    return Catalog(forms, reports=reports)
