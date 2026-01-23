# Kicad2Yolo2D
The goal of this project is to extract component placement data from the KiCad project files and use them to create YOLO training datasets.

## Scripts

### getComponents.py

This script extracts component information from KiCad PCB files (.kicad_pcb format).

**What it does:**
- Reads .kicad_pcb files from the `scr/input/` folder
- Parses footprint sections to find components
- Extracts bounding box information from the F.CrtYd (Front Courtyard) layer
- Calculates component centers and bounding boxes
- Generates a `components.csv` file with the extracted data

**Usage:**
```bash
cd scr
python getComponents.py
```

**Output:**
The script creates `scr/input/components.csv` containing:
- `name`: Component footprint name
- `center_x`, `center_y`: Component center coordinates (mm)
- `bbox_center_x`, `bbox_center_y`: Bounding box center coordinates (mm)
- `width`, `height`: Component dimensions (mm)

**Example output:**
```
name,center_x,center_y,bbox_center_x,bbox_center_y,width,height
Crystal:Crystal_HC49-4H_Vertical,135.5741,105.5116,133.1241,105.5116,12.1,5.6
Package_DFN_QFN:VQFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm,134.1501,97.0026,134.1501,97.0026,6.26,6.26
...
```
