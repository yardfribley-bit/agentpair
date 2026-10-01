#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build/AppLens.app/Contents/MacOS
clang -fobjc-arc -framework Cocoa -o build/AppLens.app/Contents/MacOS/AppLens AppLens.m
cp Info.plist build/AppLens.app/Contents/Info.plist
build/AppLens.app/Contents/MacOS/AppLens --self-test
ditto -c -k --keepParent build/AppLens.app build/AppLens-macOS.zip
