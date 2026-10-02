#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build/AppLens.app/Contents/MacOS
mkdir -p build/AppLens.app/Contents/Resources
cp ../assets/applens/applens.icns build/AppLens.app/Contents/Resources/AppLens.icns
cp workbuddy_telemetry.py build/AppLens.app/Contents/Resources/
cp telemetry.html build/AppLens.app/Contents/Resources/
cp workbuddy_hook.py install_workbuddy_hooks.py build/AppLens.app/Contents/Resources/
cp workbuddy_context.py build/AppLens.app/Contents/Resources/
cp workbuddy_network_context.py build/AppLens.app/Contents/Resources/
cp collector.html build/AppLens.app/Contents/Resources/
cp ../client-ui/capture.html build/AppLens.app/Contents/Resources/
clang -fobjc-arc -framework Cocoa -framework WebKit -framework Security -o build/AppLens.app/Contents/MacOS/AppLens AppLens.m
cp Info.plist build/AppLens.app/Contents/Info.plist
build/AppLens.app/Contents/MacOS/AppLens --self-test
ditto -c -k --keepParent build/AppLens.app build/AppLens-macOS.zip
