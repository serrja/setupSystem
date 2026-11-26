# Workstation package

The package consumes the host and SDK wheels and one reviewed descriptor per bundled plugin. Each descriptor sits next to its digest-pinned wheels. The build installs protected launchers, execution records, local catalogs and documentation into a staging directory.

```sh
uv run --locked python plugins/debian/packaging/build.py \
    --source "$commit" --version "$version" --host-wheel "$host" --sdk-wheel "$sdk" \
    --release "$catalog/debian.json" --release "$catalog/git.json" --output "$output"
python3 plugins/debian/tooling/verify_native.py "$output/setup-system_${version}_all.deb"
```

The release job accepts the attested canonical host wheel. The plugin builds locked wheel closures for both supported workstation architectures and publishes the companion only after the canonical release passes verification. The loader verifies the package signer and checks each published catalog asset against the signed archive. [Installation](install.md)

Run `make -C plugins/debian -f ci/Makefile check` from the repository root for package-owned checks. Verify boot, graphics and authorization changes on a separately allocated disposable workstation.

The package includes the project license at `/usr/share/doc/setup-system/copyright`. Debian supplies license notices for its runtime dependencies under `/usr/share/doc`.
