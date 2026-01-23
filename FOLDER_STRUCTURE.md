# Folder Structure

This document explains the organization of folders in this project.

## scr/

The main source directory for the project.

### scr/input/

This directory is designated for placing KiCad project files that will be processed to extract component placement data and create YOLO training datasets.

**Usage:**
- Place your KiCad project files (.kicad_pro, .kicad_pcb, etc.) in this directory
- The project will read these files to generate YOLO training data
