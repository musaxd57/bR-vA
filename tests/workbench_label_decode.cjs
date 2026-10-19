"use strict";
/*
 * Independent acceptance: application SVG -> resvg PNG -> ZXing Code128Reader.
 * No application pattern table, symbol sequence or checksum function is read.
 *
 * Install only under .verification-data (official npm registry, exact pins):
 * npm install --prefix .verification-data/label-decoder-20261019 --ignore-scripts
 *   --save-exact @zxing/library@0.21.3 @resvg/resvg-js@2.6.2 pngjs@7.0.0
 * Generate real SVGs with tests/workbench_label_fixtures.py first.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const ROOT = path.resolve(__dirname, "..");
const DEPS = process.env.BRIVA_LABEL_TEST_DEPS || path.join(ROOT, ".verification-data", "label-decoder-20261019", "node_modules");
const FIXTURES = process.env.BRIVA_LABEL_TEST_FIXTURES || path.join(ROOT, ".verification-data", "label-independent-20261019");
const manifestPath = path.join(FIXTURES, "manifest.json");
const ready = fs.existsSync(manifestPath) && fs.existsSync(path.join(DEPS, "@zxing", "library"));

if (!ready) {
  test("independent barcode decoder requires its retained fixture/dev dependency setup", { skip: "Run tests/workbench_label_fixtures.py and install the exact dev-only npm packages under .verification-data/." }, () => {});
} else {
  const { Resvg } = require(path.join(DEPS, "@resvg", "resvg-js"));
  const { PNG } = require(path.join(DEPS, "pngjs"));
  const { Code128Reader, RGBLuminanceSource, BinaryBitmap, HybridBinarizer, DecodeHintType, BarcodeFormat } = require(path.join(DEPS, "@zxing", "library"));
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  assert.equal(manifest.encoder_sha256, crypto.createHash("sha256").update(fs.readFileSync(path.join(ROOT, "briva_core", "labels.py"))).digest("hex"), "Regenerate SVG fixtures after changing the real encoder");
  const runId = new Date().toISOString().replace(/[:.]/g, "-");
  const output = path.join(FIXTURES, "decode-" + runId);
  fs.mkdirSync(output, { recursive: true });
  const report = {
    generated_at: new Date().toISOString(),
    methodology: "Unmodified application SVG rasterized by resvg. PNG pixels decoded by the independent ZXing Code128Reader. Exact original input text required, including leading zeros and spaces.",
    hardware_pilot: "NOT PERFORMED. Digital raster decoding does not validate a printer, scanner, media, adhesive, darkness, speed or wear.",
    libraries: { "@zxing/library": "0.21.3", "@resvg/resvg-js": "2.6.2", pngjs: "7.0.0" },
    sources: ["https://github.com/zxing-js/library", "https://github.com/yisibl/resvg-js", "https://www.npmjs.com/package/@zxing/library", "https://www.npmjs.com/package/@resvg/resvg-js"],
    standalone: [], physical_profiles: [], crisp_edge_profiles: [], negatives: [], invalid_input_rejections: manifest.invalid_inputs
  };

  function geometry(svg) {
    const match = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg);
    assert.ok(match, "SVG has a numeric viewBox");
    return { width: Number(match[1]), height: Number(match[2]), stretch: /preserveAspectRatio="none"/.test(svg), body: svg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "") };
  }

  function raster(svg, width, height, phase) {
    const source = geometry(svg);
    // The application now uses object-fit:fill and preserveAspectRatio:none.
    // Keep the old meet behavior observable for fixture comparisons, but do
    // not accidentally apply it to the new independently sized axes.
    const uniform = Math.min(width / source.width, height / source.height);
    const scale = source.stretch ? width / source.width : uniform;
    const scaleY = source.stretch ? height / source.height : uniform;
    const x = (width - source.width * scale) / 2 + (phase || 0);
    const y = (height - source.height * scaleY) / 2;
    const wrapped = '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + " " + height + '"><rect width="100%" height="100%" fill="white"/><g transform="translate(' + x + " " + y + ") scale(" + scale + " " + scaleY + ')">' + source.body + "</g></svg>";
    const renderer = new Resvg(wrapped, { background: "white", shapeRendering: 2, font: { loadSystemFonts: false, fontFiles: [path.join(ROOT, "fonts", "DejaVuSans.ttf")], defaultFontFamily: "DejaVu Sans", monospaceFamily: "DejaVu Sans" } });
    const png = renderer.render().asPng();
    return { png, image: PNG.sync.read(png), scale };
  }

  function decode(image) {
    const grey = new Uint8ClampedArray(image.width * image.height);
    for (let index = 0; index < grey.length; index += 1) {
      const offset = index * 4;
      grey[index] = (image.data[offset] + 2 * image.data[offset + 1] + image.data[offset + 2]) / 4;
    }
    const luminance = new RGBLuminanceSource(grey, image.width, image.height);
    const bitmap = new BinaryBitmap(new HybridBinarizer(luminance));
    const hints = new Map([[DecodeHintType.TRY_HARDER, true]]);
    try {
      const result = new Code128Reader().decode(bitmap, hints);
      return { decoded: result.getText(), format: result.getBarcodeFormat() === BarcodeFormat.CODE_128 ? "CODE_128" : String(result.getBarcodeFormat()) };
    } catch (error) { return { decoded: null, error: error.constructor && error.constructor.name || "decode_failure" }; }
  }

  function record(group, fixture, profile, rendered) {
    const decoded = decode(rendered.image);
    const filename = fixture.id + "--" + profile.replace(/[^a-zA-Z0-9_.-]/g, "_") + ".png";
    fs.writeFileSync(path.join(output, filename), rendered.png);
    const row = { case: fixture.id, input: fixture.code, length: fixture.length, production_shape: fixture.production_shape, profile, width_px: rendered.image.width, height_px: rendered.image.height, pixels_per_narrow_module: Number(rendered.scale.toFixed(4)), image: filename, ...decoded };
    row.matches = decoded.decoded === fixture.code;
    report[group].push(row);
    if (decoded.decoded !== null) assert.equal(decoded.decoded, fixture.code, "Decoder must never return a different barcode: " + fixture.id + " " + profile);
    return row;
  }

  test("independent ZXing decodes all real SVG character/checksum cases at usable raster scales", () => {
    for (const fixture of manifest.cases) {
      const svg = fs.readFileSync(path.join(FIXTURES, fixture.file), "utf8");
      const box = geometry(svg);
      for (const scale of [2, 3, 4]) {
        for (const phase of [0, 0.5]) {
          const rendered = raster(svg, Math.ceil(box.width * scale + 2), Math.ceil(box.height * scale + 2), phase);
          const row = record("standalone", fixture, "module-" + scale + "px-phase-" + phase, rendered);
          // Two-pixel modules are measured, not promised: half-pixel raster
          // alignment may erase their only fully dark interior. The encoder's
          // independent round-trip gate uses at least three pixels/module.
        }
      }
    }
    const failures = report.standalone.filter((row) => row.pixels_per_narrow_module >= 3 && !row.matches);
    assert.deepEqual(failures.map((row) => ({ case: row.case, profile: row.profile })), [], "All character/checksum cases must decode at >=3px/module");
  });

  test("current label sizes are evaluated at 203/300 DPI with actual SVG and CSS sizing", () => {
    // Matches static/workbench.css: label width/padding and barcode image height.
    const profiles = [
      { label: "50x30", innerWidthMm: 46, imageHeightMm: 11 },
      { label: "60x40", innerWidthMm: 54, imageHeightMm: 17 },
      { label: "70x40", innerWidthMm: 64, imageHeightMm: 17 }
    ];
    for (const fixture of manifest.cases) {
      const svg = fs.readFileSync(path.join(FIXTURES, fixture.file), "utf8");
      for (const profile of profiles) {
        for (const dpi of [203, 300]) {
          const width = Math.round(profile.innerWidthMm * dpi / 25.4);
          const height = Math.round(profile.imageHeightMm * dpi / 25.4);
          for (const phase of [0, 0.5]) {
            const rendered = raster(svg, width, height, phase);
            const row = record("physical_profiles", fixture, profile.label + "-" + dpi + "dpi-phase-" + phase, rendered);
            row.label = profile.label; row.dpi = dpi; row.phase = phase;
            row.narrow_module_mm = Number((rendered.scale * 25.4 / dpi).toFixed(4));
            // Default-size production barcodes need to pass at a useful 300-DPI
            // digital resolution. Other sizes characterize limits honestly.
            if (fixture.production_shape && profile.label === "60x40" && dpi === 300) assert.equal(row.matches, true, "Default 60x40 / 300 DPI production case: " + fixture.id);
          }
        }
      }
    }
  });

  test("blank or heavily occluded images do not yield a false valid barcode", () => {
    const fixture = manifest.cases.find((item) => item.id === "unit-default");
    const svg = fs.readFileSync(path.join(FIXTURES, fixture.file), "utf8");
    const box = geometry(svg);
    for (const name of ["blank", "central-occlusion"]) {
      const rendered = raster(svg, box.width * 3, box.height * 3, 0);
      const image = rendered.image;
      const from = name === "blank" ? 0 : Math.floor(image.width / 3);
      const to = name === "blank" ? image.width : Math.ceil(2 * image.width / 3);
      for (let y = 0; y < image.height; y += 1) for (let x = from; x < to; x += 1) {
        const offset = (y * image.width + x) * 4;
        image.data[offset] = 255; image.data[offset + 1] = 255; image.data[offset + 2] = 255; image.data[offset + 3] = 255;
      }
      const result = decode(image);
      fs.writeFileSync(path.join(output, name + ".png"), PNG.sync.write(image));
      report.negatives.push({ case: name, ...result, rejected: result.decoded === null });
      assert.equal(result.decoded, null, name + " must not produce a valid result");
    }
  });

  test("crispEdges rasterization is independently checked without changing encoder code", () => {
    for (const fixture of manifest.cases) {
      const original = fs.readFileSync(path.join(FIXTURES, fixture.file), "utf8");
      const svg = original.replace(/<g fill="black"[^>]*>/, '<g fill="black" shape-rendering="crispEdges">');
      assert.ok(svg.includes('<g fill="black" shape-rendering="crispEdges">'), "Black bar group must explicitly request crisp raster edges");
      for (const profile of [{ label: "50x30", width: 46, height: 11 }, { label: "60x40", width: 54, height: 17 }, { label: "70x40", width: 64, height: 17 }]) for (const dpi of [203, 300]) for (const phase of [0, 0.5]) {
        const rendered = raster(svg, Math.round(profile.width * dpi / 25.4), Math.round(profile.height * dpi / 25.4), phase);
        const row = record("crisp_edge_profiles", fixture, "crispEdges-" + profile.label + "-" + dpi + "dpi-phase-" + phase, rendered);
        row.label = profile.label; row.dpi = dpi; row.phase = phase;
      }
    }
  });

  test("every profile permitted by the actual workbench guard decodes independently", () => {
    const Module = require("node:module");
    const jsPath = path.join(ROOT, "static", "js", "workbench.js");
    const compiled = new Module(jsPath, module);
    compiled.filename = jsPath;
    compiled._compile(fs.readFileSync(jsPath, "utf8"), jsPath);
    const workbench = compiled.exports;
    const geometries = new Map(manifest.cases.map((fixture) => [fixture.id, workbench.parseBarcodeGeometry(fs.readFileSync(path.join(FIXTURES, fixture.file), "utf8"))]));
    const allowed = report.physical_profiles.filter((row) => workbench.labelPrintQuality(geometries.get(row.case), row.label, row.dpi).allowed);
    assert.ok(allowed.length > 0, "The print guard must allow usable profiles");
    assert.deepEqual(allowed.filter((row) => !row.matches).map((row) => ({ case: row.case, profile: row.profile })), [], "A digitally unreadable tested profile must not be offered as printable");
    report.guard_acceptance = { permitted_profiles: allowed.length, independently_decoded: allowed.filter((row) => row.matches).length, hardware_pilot: false };
  });

  test.after(() => {
    report.summary = {
      standalone: { tested: report.standalone.length, decoded: report.standalone.filter((row) => row.matches).length },
      physical_profiles: { tested: report.physical_profiles.length, decoded: report.physical_profiles.filter((row) => row.matches).length, unreadable: report.physical_profiles.filter((row) => !row.matches).length },
      crisp_edge_profiles: { tested: report.crisp_edge_profiles.length, decoded: report.crisp_edge_profiles.filter((row) => row.matches).length, unreadable: report.crisp_edge_profiles.filter((row) => !row.matches).length },
      unreadable_production_profiles: report.physical_profiles.filter((row) => row.production_shape && !row.matches).map((row) => ({ case: row.case, profile: row.profile, pixels_per_narrow_module: row.pixels_per_narrow_module })),
      unreadable_max_length_profiles: report.physical_profiles.filter((row) => row.case === "max-48" && !row.matches).map((row) => row.profile)
    };
    fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write("Independent barcode report: " + path.join(output, "report.json") + "\n");
    process.stdout.write(JSON.stringify(report.summary) + "\n");
  });
}
