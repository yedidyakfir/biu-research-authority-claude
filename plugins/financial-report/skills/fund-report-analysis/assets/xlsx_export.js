/* Browser-side, dependency-free XLSX export with shared strings. */
(function (root) {
  "use strict";

  const enc = new TextEncoder();
  const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';

  function xml(value) {
    return String(value ?? "")
      .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&apos;");
  }

  function colName(index) {
    let n = index + 1;
    let out = "";
    while (n) { n--; out = String.fromCharCode(65 + n % 26) + out; n = Math.floor(n / 26); }
    return out;
  }

  function asCell(value) {
    if (value == null) return null;
    if (typeof value === "object" && "t" in value) return value;
    if (typeof value === "boolean") return { t: "b", v: value };
    if (typeof value === "number") return { t: "n", v: value };
    return { t: "s", v: String(value) };
  }

  function prepareSheets(report) {
    const selected = report.source.sheet;
    const byRow = new Map(report.records.map(record => [record.row, record]));
    const categories = new Map(report.foundation.categories.map(item => [item.id, item]));
    const headings = ["סיווג מוצע", "מצב בדיקה", "נימוק", "סיבת אי-ודאות", "מקור כללים", "זכאות לפי תקציב מאושר"];
    const statusLabels = { classified: "סווג", needs_review: "דורש בדיקה", not_expense: "לא הוצאה" };
    return report.sheets.map(sheet => {
      const width = Math.max(1, ...sheet.rows.map(row => row.length));
      const rows = sheet.rows.map((sourceRow, index) => {
        const row = sourceRow.map(asCell);
        if (sheet.name !== selected) return row;
        while (row.length < width) row.push(null);
        if (index + 1 === report.source.header_row) {
          headings.forEach((heading, i) => { row[width + i] = asCell(heading); });
        } else {
          const record = byRow.get(index + 1);
          if (record && record.kind === "transaction") {
            const c = record.classification;
            const category = categories.get(c.category_id);
            const values = [
              category ? category.label_he : "",
              statusLabels[c.status] || c.status,
              c.rationale || "",
              c.uncertainty_reason || "",
              report.foundation.source.url || "",
              "לא אומתה",
            ];
            values.forEach((value, i) => { row[width + i] = asCell(value); });
          }
        }
        return row;
      });
      return { name: sheet.name, rows };
    });
  }

  function sharedStrings(sheets) {
    const map = new Map();
    let count = 0;
    sheets.forEach(sheet => sheet.rows.forEach(row => row.forEach(cell => {
      if (cell && cell.t === "s") {
        count++;
        const value = String(cell.v ?? "");
        if (!map.has(value)) map.set(value, map.size);
      }
    })));
    const items = [...map.keys()].map(value => `<si><t xml:space="preserve">${xml(value)}</t></si>`).join("");
    return { map, content: `${XML}<sst xmlns="${NS}" count="${count}" uniqueCount="${map.size}">${items}</sst>` };
  }

  function sheetXml(sheet, strings) {
    const maxCol = Math.max(1, ...sheet.rows.map(row => row.length));
    const last = `${colName(maxCol - 1)}${Math.max(1, sheet.rows.length)}`;
    const rowXml = [];
    sheet.rows.forEach((row, ri) => {
      const cells = [];
      row.forEach((cell, ci) => {
        if (!cell) return;
        const ref = `${colName(ci)}${ri + 1}`;
        if (cell.t === "s") {
          cells.push(`<c r="${ref}" t="s"><v>${strings.get(String(cell.v ?? ""))}</v></c>`);
        } else if (cell.t === "b") {
          cells.push(`<c r="${ref}" t="b"><v>${cell.v ? 1 : 0}</v></c>`);
        } else if (cell.t === "f") {
          cells.push(`<c r="${ref}"><f>${xml(String(cell.v).replace(/^=/, ""))}</f></c>`);
        } else if ((cell.t === "n" || cell.t === "d") && Number.isFinite(Number(cell.v))) {
          cells.push(`<c r="${ref}"${cell.t === "d" ? ' s="1"' : ""}><v>${Number(cell.v)}</v></c>`);
        }
      });
      if (cells.length) rowXml.push(`<row r="${ri + 1}">${cells.join("")}</row>`);
    });
    return `${XML}<worksheet xmlns="${NS}"><dimension ref="A1:${last}"/><sheetViews><sheetView workbookViewId="0" rightToLeft="1"/></sheetViews><sheetFormatPr defaultRowHeight="15"/><sheetData>${rowXml.join("")}</sheetData></worksheet>`;
  }

  function crc32(bytes) {
    let crc = -1;
    for (const byte of bytes) {
      crc ^= byte;
      for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1));
    }
    return (crc ^ -1) >>> 0;
  }

  function concat(parts) {
    const size = parts.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(size);
    let at = 0;
    for (const part of parts) { out.set(part, at); at += part.length; }
    return out;
  }

  function zip(entries) {
    const files = [];
    const directory = [];
    let offset = 0;
    for (const [name, content] of entries) {
      const filename = enc.encode(name);
      const bytes = enc.encode(content);
      const crc = crc32(bytes);
      const local = new Uint8Array(30 + filename.length);
      const l = new DataView(local.buffer);
      l.setUint32(0, 0x04034b50, true); l.setUint16(4, 20, true); l.setUint16(6, 0x0800, true);
      l.setUint16(8, 0, true); l.setUint16(12, 0x0021, true); l.setUint32(14, crc, true); l.setUint32(18, bytes.length, true);
      l.setUint32(22, bytes.length, true); l.setUint16(26, filename.length, true);
      local.set(filename, 30);
      files.push(local, bytes);
      const central = new Uint8Array(46 + filename.length);
      const c = new DataView(central.buffer);
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true);
      c.setUint16(8, 0x0800, true); c.setUint16(10, 0, true); c.setUint16(14, 0x0021, true); c.setUint32(16, crc, true);
      c.setUint32(20, bytes.length, true); c.setUint32(24, bytes.length, true);
      c.setUint16(28, filename.length, true); c.setUint32(42, offset, true);
      central.set(filename, 46);
      directory.push(central);
      offset += local.length + bytes.length;
    }
    const centralSize = directory.reduce((n, p) => n + p.length, 0);
    const end = new Uint8Array(22);
    const e = new DataView(end.buffer);
    e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true);
    e.setUint16(10, entries.length, true); e.setUint32(12, centralSize, true);
    e.setUint32(16, offset, true);
    return concat([...files, ...directory, end]);
  }

  function buildXlsx(report) {
    const sheets = prepareSheets(report);
    const strings = sharedStrings(sheets);
    const overrides = sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("");
    const types = `${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>${overrides}</Types>`;
    const rootRels = `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
    const workbook = `${XML}<workbook xmlns="${NS}" xmlns:r="${REL}"><sheets>${sheets.map((sheet, i) => `<sheet name="${xml(sheet.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets></workbook>`;
    const rels = `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="${REL}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}<Relationship Id="rId${sheets.length + 1}" Type="${REL}/styles" Target="styles.xml"/><Relationship Id="rId${sheets.length + 2}" Type="${REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>`;
    const styles = `${XML}<styleSheet xmlns="${NS}"><fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
    const entries = [["[Content_Types].xml", types], ["_rels/.rels", rootRels], ["xl/workbook.xml", workbook], ["xl/_rels/workbook.xml.rels", rels], ["xl/styles.xml", styles], ["xl/sharedStrings.xml", strings.content]];
    sheets.forEach((sheet, i) => entries.push([`xl/worksheets/sheet${i + 1}.xml`, sheetXml(sheet, strings.map)]));
    return zip(entries);
  }

  const api = { buildXlsx, prepareSheets };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.FundXlsx = api;
})(typeof window === "undefined" ? globalThis : window);
