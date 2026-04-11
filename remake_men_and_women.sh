#!/bin/bash
printf '%s\0' "/run/media/Flan/Big E/Proyectos/Tools/BlenderScripts/Voxelator/Ultimate Modular Men Pack-zip"/*/ "/run/media/Flan/Big E/Proyectos/Tools/BlenderScripts/Voxelator/Ultimate Modular Women Pack-zip"/*/ | parallel -0 --jobs 16 --bar python "/run/media/Flan/Big\ E/Proyectos/Tools/BlenderScripts/Voxelator/run_voxelator_batch.py" --input-dir "{}" --res 60 --action All --frame-step 2 --clean-output --rot-offset 90

