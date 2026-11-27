# 📦 Workstation package

The package consumes the attested host wheel, SDK wheel and one reviewed descriptor per bundled plugin. It includes verified uv executables and complete locked host and native wheel closures for amd64 and arm64. The build installs protected launchers, execution records, local catalogs and documentation into a staging directory. APT depends only on system Python, CA certificates, util-linux and sudo; uv prepares private Python runtimes from package-owned wheels without contacting an index.

```sh
set --
for descriptor in "$catalog"/*.json; do set -- "$@" --release "$descriptor"; done
uv run --locked --package setup-system-plugin-debian python plugins/debian/packaging/build.py \
    --source "$commit" --version "$version" --host-wheel "$host" --sdk-wheel "$sdk" \
    --runtime-assets "$catalog/runtime" "$@" --output "$output"
python3 plugins/debian/tooling/verify_native.py "$output/setup-system_${version}_all.deb"
```

The release job accepts the attested canonical host wheel. The plugin builds locked wheel closures and verifies upstream uv archive digests for both supported workstation architectures. The package's `postinst` prepares protected host and native venvs and publishes their active pointer only after dependency and import checks. The loader verifies the package signer and checks each published catalog asset against the signed archive. [Installation](install.md)

The Greeter login helper and Hyprland panel are separate user-session programs that use system CPython and the standard library. The Python toolchain plugin may also select system CPython as the interpreter for a user-confirmed developer-tool environment. None imports application libraries from system site packages.

Run `make -C plugins/debian -f ci/Makefile check` from the repository root for package-owned checks. The build check verifies both architecture inventories and imports host, native handlers and each bundled plugin from separate locked environments on the CI runner's architecture. Verify boot, graphics and authorization changes on a separately allocated disposable workstation.

The package includes the project license at `/usr/share/doc/setup-system/copyright`. Debian supplies license notices for its runtime dependencies under `/usr/share/doc`.
