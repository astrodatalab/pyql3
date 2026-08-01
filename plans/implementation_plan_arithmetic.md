# Image Arithmetic Tool Implementation Plan

This plan details the implementation of the `Math -> Arithmetic...` tool, replicating the functionality of the original IDL `ql_conmath` tool.

## User Review Required
The original tool spawns a completely new window to display the calculated result. 
- **Decision:** I propose modifying the main application architecture slightly so we can spawn a **new independent instance** of the `MainWindow` that holds the resulting array in-memory. This way, you get all the features of `pyql3` (Depth Plots, Cuts, etc.) on the calculated result without having to save it to disk first. Does this sound like the right approach for the "new window" requirement?

## Proposed Changes

### 1. `ArithmeticDialog` (UI & Logic)
- **File:** Create a new tool file `pyql3/gui/tools/arithmetic.py`.
- **UI Layout:**
  - Create a dialog window with a horizontal layout for three columns: **Operand 1**, **Operation**, and **Operand 2**.
  - **Operand Columns:** Each will have radio buttons for `[File, Number, Active Image]`.
    - `File`: Includes a file path input box and a `Browse` button.
    - `Number`: Includes a numeric input spinner.
    - `Active Image`: Selects the currently loaded data cube in the parent `MainWindow`.
  - **Operation Column:** Radio buttons or a combo box for `[ +, -, *, / ]`.
  - **Bottom Buttons:** `Calculate` and `Close`.
- **Calculation Logic:**
  - When `Calculate` is pressed, the tool will extract `data1` and `data2` (either as `float` scalars or numpy arrays).
  - For arrays, it will check that the dimensions match.
  - Perform the arithmetic operation using `numpy`. For division, it will safely handle division-by-zero using `np.errstate` to yield `NaN`s instead of crashing.
  - Construct a new FITS header inherited from the primary operand (Operand 1, or Operand 2 if Operand 1 is a scalar), adding a `HISTORY` line denoting the mathematical operation performed.

### 2. In-Memory FITS Loading (`main_window.py` & `fits_reader.py`)
- Modify `FitsReader` in `pyql3/core/fits_reader.py` to support loading an `astropy.io.fits.HDUList` directly from memory instead of just from a filepath.
- Modify `MainWindow` to accept a memory-loaded `FitsReader` or raw `data`/`header` so we can instantiate a new application window for the arithmetic result:
  ```python
  # Example logic in arithmetic.py
  result_window = MainWindow()
  result_window.load_from_memory(result_data, result_header, title="(A - B)")
  result_window.show()
  ```

### 3. Connect the Menu
- In `main_window.py`, connect the existing placeholder `math_menu.addAction("Arithmetic...")` to a new method `open_arithmetic_tool` which will spawn the `ArithmeticDialog`.

## Verification Plan
1. Launch `pyql3` and load an active image.
2. Open `Math -> Arithmetic...`.
3. Set Operand 1 to `Active Image`, Operation to `-`, and Operand 2 to `Number` (e.g., 500).
4. Click `Calculate`.
5. Verify a new `MainWindow` spawns titled with the operation, showing the data cube with 500 subtracted from every pixel.
6. Verify you can perform Depth Plots on the new arithmetic result window!
