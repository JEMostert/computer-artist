#!/bin/bash
# fetch-fonts.sh : download the reel's OFL fonts from google/fonts and make the static cuts it uses.
# Needs Python with fonttools (pip install fonttools).
set -eu
R=$(cd "$(dirname "$0")" && pwd); mkdir -p "$R/fonts"; cd "$R/fonts"
G=https://raw.githubusercontent.com/google/fonts/main/ofl
curl -sSfL -o Archivo.ttf "$G/archivo/Archivo%5Bwdth,wght%5D.ttf"
curl -sSfL -o JetBrainsMono.ttf "$G/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"
curl -sSfL -o InstrumentSerif-Italic.ttf "$G/instrumentserif/InstrumentSerif-Italic.ttf"
python3 -m fontTools.varLib.instancer Archivo.ttf wdth=125 wght=900 -o ArchivoXB.ttf -q
python3 -m fontTools.varLib.instancer Archivo.ttf wdth=125 wght=500 -o ArchivoXM.ttf -q
python3 -m fontTools.varLib.instancer JetBrainsMono.ttf wght=500 -o Mono-500.ttf -q
python3 -m fontTools.varLib.instancer JetBrainsMono.ttf wght=800 -o Mono-800.ttf -q
rm Archivo.ttf JetBrainsMono.ttf
