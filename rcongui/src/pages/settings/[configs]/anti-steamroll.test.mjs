import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";
import { transformWithOxc } from "vite";

// Exercise the real shared page loader/action with isolated HTTP and rendering
// boundaries. No browser, running backend, or additional test dependency is needed.
async function configPage({ get, execute }) {
  const context = createContext({ FormData });
  const pageUrl = new URL("./detail.jsx", import.meta.url);
  const source = await readFile(pageUrl, "utf8");
  const { code } = await transformWithOxc(source, pageUrl.pathname, {
    lang: "jsx",
    jsx: { runtime: "automatic" },
  });
  const mocks = {
    "react-router-dom": {
      Form: undefined,
      json: (data) => data,
      useActionData: undefined,
      useLoaderData: undefined,
      useRevalidator: undefined,
      useSubmit: undefined,
    },
    react: {
      lazy: () => undefined,
      Suspense: undefined,
      useEffect: undefined,
      useState: undefined,
    },
    "react/jsx-runtime": {
      jsx: undefined,
      jsxs: undefined,
      Fragment: undefined,
    },
    "@/utils/fetchUtils": {
      get,
      execute,
      handleHttpError: (error) => {
        throw error;
      },
    },
    "@mui/material": {
      Box: undefined,
      Button: undefined,
      Stack: undefined,
      Typography: undefined,
      useTheme: undefined,
    },
    "react-code-blocks": {
      a11yDark: undefined,
      a11yLight: undefined,
      CopyBlock: undefined,
    },
    "react-toastify": { toast: undefined },
    "@mui/material/colors": { red: undefined },
    "@jsonforms/react": { JsonForms: undefined },
    "@jsonforms/material-renderers": {
      materialCells: undefined,
      materialRenderers: undefined,
    },
    "@jsonforms/core": { Generate: undefined },
    "@mui/material/ButtonGroup": { default: undefined },
    "@mui/material/styles": {
      createTheme: undefined,
      ThemeProvider: undefined,
    },
    "@/pages/settings/[configs]/renderer/renderer": {
      customRenderers: undefined,
    },
  };
  const page = new SourceTextModule(code, {
    context,
    identifier: pageUrl.href,
    importModuleDynamically: async (specifier) => {
      const url = new URL(
        specifier.endsWith(".js") ? specifier : `${specifier}.js`,
        pageUrl
      );
      const module = new SourceTextModule(await readFile(url, "utf8"), {
        context,
      });
      await module.link(() => {
        throw new Error("Unexpected documentation import");
      });
      await module.evaluate();
      return module;
    },
  });
  await page.link((specifier) => {
    assert.ok(specifier in mocks, `Unexpected page dependency: ${specifier}`);
    const values = mocks[specifier];
    return new SyntheticModule(
      Object.keys(values),
      function () {
        for (const [key, value] of Object.entries(values))
          this.setExport(key, value);
      },
      { context }
    );
  });
  await page.evaluate();
  return page.namespace;
}

test("Anti-Steamroll route loads its configuration and visual schema", async () => {
  const calls = [];
  const config = { enabled: true, immune_roles: ["sniper"] };
  const schema = {
    type: "object",
    properties: { enabled: { type: "boolean" } },
  };
  const page = await configPage({
    get: async (command) => {
      calls.push(command);
      return Response.json({
        failed: false,
        result: command.startsWith("describe_") ? schema : config,
      });
    },
  });
  const data = await page.loader({
    params: { category: "automods", type: "anti-steamroll" },
  });
  assert.equal(data.name, "Anti-Steamroll");
  assert.equal(data.command, "auto_mod_antisteamroll_config");
  assert.deepEqual(data.data, config);
  assert.deepEqual(data.schema, schema);
  assert.match(data.note, /entire final sector/);
  assert.match(data.note, /immune_roles/);
  assert.deepEqual(calls, [
    "get_auto_mod_antisteamroll_config",
    "describe_auto_mod_antisteamroll_config",
  ]);
});

test("Anti-Steamroll form validates then saves live settings", async () => {
  const calls = [];
  const config = { enabled: true, allow_final_attack_remaining_minutes: 20 };
  const page = await configPage({
    execute: async (command, data) => {
      calls.push({ command, data });
      return Response.json({ failed: false, error: null });
    },
  });
  const form = new FormData();
  form.set("name", "auto_mod_antisteamroll_config");
  form.set("config", JSON.stringify(config));
  const result = await page.action({ request: { formData: async () => form } });
  assert.equal(result.ok, true);
  assert.deepEqual(
    calls.map(({ command }) => command),
    [
      "validate_auto_mod_antisteamroll_config",
      "set_auto_mod_antisteamroll_config",
    ]
  );
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), config);
});

test("Anti-Steamroll form does not save rejected configuration", async () => {
  const calls = [];
  const page = await configPage({
    execute: async (command) => {
      calls.push(command);
      return Response.json({
        failed: false,
        error: "Roles are not valid for hll: notarole",
      });
    },
  });
  const form = new FormData();
  form.set("name", "auto_mod_antisteamroll_config");
  form.set("config", JSON.stringify({ immune_roles: ["notarole"] }));
  const result = await page.action({ request: { formData: async () => form } });
  assert.equal(result.ok, false);
  assert.deepEqual(calls, ["validate_auto_mod_antisteamroll_config"]);
});
