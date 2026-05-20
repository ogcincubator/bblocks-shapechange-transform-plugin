# bblocks-shapechange-transform-plugin

A [bblocks transform plugin](https://opengeospatial.github.io/bblocks/create/transforms#transform-plugins)
that adds a `shapechange` transform type, allowing Enterprise Architect model files to be processed
by [ShapeChange](https://shapechange.net) during building block postprocessing.

## Requirements

The input must be a **SQLite3-based EA model file** — i.e. `.eapx` or `.qea` format. The old binary
`.eap` format requires Enterprise Architect to be installed and is not supported. Most modern EA
installations can save in `.eapx` format.

A JRE (Temurin 21) and the ShapeChange JAR are downloaded automatically on first use and cached in
the plugin's sandbox directory. Subsequent runs reuse the cached binaries.

## Usage

Add the plugin to `transform-plugins.yml` in your building blocks repository:

```yaml
plugins:
  - pip: git+https://github.com/ogcincubator/bblocks-shapechange-transform-plugin.git
    modules:
      - bbplugin_shapechange
```

Then declare a `shapechange` transform in your building block's `transforms.yaml`:

```yaml
transforms:
  - id: to-json-schema
    type: shapechange
    inputs:
      mediaTypes:
        - mimeType: application/x-ea-eap
          defaultExtension: eapx
    outputs:
      mediaTypes:
        - mimeType: application/zip
          defaultExtension: zip
      profiles:
        - https://json-schema.org/draft/2020-12/schema
    ref: transforms/shapechange-config.xml
```

The `ref` (or inline `code`) must be a ShapeChange XML configuration file. Use the following
placeholders — the plugin substitutes them at runtime:

| Placeholder | Replaced with |
|-------------|---------------|
| `{input_file}` | Absolute path to the input model file |
| `{output_dir}` | Absolute path to the directory where ShapeChange should write its outputs |

### Example configuration

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ShapeChangeConfiguration>
  <input>
    <parameter name="inputModelType" value="EA7"/>
    <parameter name="repositoryFileNameOrConnectionString" value="{input_file}"/>
    <parameter name="appSchemaName" value="My Application Schema"/>
  </input>
  <targets>
    <Target class="de.interactive_instruments.ShapeChange.Target.JSONSchema.JsonSchemaTarget"
            mode="enabled">
      <targetParameter name="outputDirectory" value="{output_dir}"/>
      <targetParameter name="jsonSchemaVersion" value="2019-09"/>
    </Target>
  </targets>
</ShapeChangeConfiguration>
```

The plugin ZIPs everything written to `{output_dir}` and returns it as the transform output.
If ShapeChange produces no output files, the transform produces no output (not an error).

## Output

The transform produces `application/zip`. Use `outputs.profiles` to annotate what the ZIP contains,
e.g.:

```yaml
outputs:
  mediaTypes:
    - mimeType: application/zip
      defaultExtension: zip
  profiles:
    - https://json-schema.org/draft/2020-12/schema
```