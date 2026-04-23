import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer, RisingEdge
import pandas as pd 
import numpy as np
from scipy import signal
import matplotlib.pyplot as plt

@cocotb.test()
async def adc_chain_tb(dut):
    total_cycles = 20000

    # 1. Setup Clock & Reset
    clock = Clock(dut.clk, 1, unit="ns")
    cocotb.start_soon(clock.start()) 

    dut.rst.value = 1
    dut.i_data.value = 0
    dut.i_ready.value = 0
    await Timer(10, "ns") 
    await RisingEdge(dut.clk)
    dut.rst.value = 0          # Release reset

    # 2. Setup Data Collection Arrays
    i_data_arr = []
    cic_data_arr = []          # New: For CIC output
    comp_data_arr = []         # New: For Compensation FIR output
    o_data_arr = []

    # Generate test signal (PWM simulating a Sigma-Delta bitstream)
    # --- Non-ideal Sigma-Delta-like signal generation ---

    t = np.linspace(0, 1, total_cycles, endpoint=False)

    # --- 1. Clock jitter ---
    jitter_std = 0.00001
    t_jittered = t + np.random.normal(0, jitter_std, size=len(t))

    # --- Base signal ---
    freq = 5  # keep within passband!
    sig = np.sin(2 * np.pi * freq * t_jittered)

    # --- 2. Analog noise ---
    noise_std = 0.05
    sig_noisy = sig + np.random.normal(0, noise_std, size=len(sig))

    # Clamp to [-1, 1]
    sig_noisy = np.clip(sig_noisy, -1, 1)

    # --- PWM generation ---
    pwm = signal.square(2 * np.pi * 1000 * t_jittered, duty=(sig_noisy + 1)/2)

    # Convert to {-1, +1}
    pwm = pwm.astype(int)

    # --- 3. Comparator noise (bit flips) ---
    flip_prob = 0.01
    flip_mask = np.random.rand(len(pwm)) < flip_prob
    pwm[flip_mask] *= -1

    # --- 4. Dropouts / glitches ---
    drop_prob = 0.005
    for i in range(1, len(pwm)):
        if np.random.rand() < drop_prob:
            pwm[i] = pwm[i-1]  # hold previous value

    # 3. Main Test Loop
    for i in range(total_cycles):
        await RisingEdge(dut.clk)

        # Drive Inputs
        val = int(pwm[i])
        dut.i_data.value = val
        dut.i_ready.value = 1

        i_data_arr.append(val)

        # --- Capture Intermediate and Final Outputs ---

        # 1. CIC Output (Valid every 100 clocks)
        if dut.cic_ready.value == 1:
            cic_data_arr.append(dut.truncated_cic_data.value.to_signed())

        # 2. Compensation FIR Output (Valid every 100 clocks)
        if dut.comp_ready.value == 1:
            comp_data_arr.append(dut.comp_data.value.to_signed())

        # 3. Final Half-band Output (Valid every 200 clocks)
        if dut.o_ready.value == 1:
            o_data_arr.append(dut.o_data.value.to_signed())

    print(f"Test complete. Captured outputs: CIC={len(cic_data_arr)}, Comp={len(comp_data_arr)}, Final={len(o_data_arr)}")

    # --- 4. Plotting the Results ---
    plt.figure(figsize=(12, 10))

    # Plot 1: Input PWM (Zoomed in)
    # Plotting 20,000 square waves just looks like a solid block of color, 
    # so we only plot the first 1000 samples to see the pulse widths.
    plt.subplot(4, 1, 1)
    plt.plot(i_data_arr[:1000], drawstyle='steps-post', color='red')
    plt.title('Stage 1: Input PWM Signal (Zoomed to first 1000 cycles)')
    plt.grid(True)

    # Plot 2: Truncated CIC Output
    plt.subplot(4, 1, 2)
    plt.plot(cic_data_arr, color='orange')
    plt.title(f'Stage 2: CIC Filter Output (Decimated by 100, Samples: {len(cic_data_arr)})')
    plt.grid(True)

    # Plot 3: Compensation FIR Output
    plt.subplot(4, 1, 3)
    plt.plot(comp_data_arr, color='green')
    plt.title(f'Stage 3: Compensation FIR Output (Samples: {len(comp_data_arr)})')
    plt.grid(True)

    # --- 4. Plotting the Results ---

    # Calculate time vectors for each stage based on their sample rates
    fs_in = 20000
    fs_cic = fs_in / 100    # 200 Hz
    fs_comp = fs_in / 100   # 200 Hz
    fs_final = fs_in / 200  # 100 Hz

    t_in = np.arange(len(i_data_arr)) / fs_in
    t_cic = np.arange(len(cic_data_arr)) / fs_cic
    t_comp = np.arange(len(comp_data_arr)) / fs_comp
    t_final = np.arange(len(o_data_arr)) / fs_final

    # Set a shared time window to view (0.2 seconds = 2 cycles of 10 Hz)
    view_window = 0.2

    plt.figure(figsize=(12, 10))

    # Plot 1: Input PWM
    plt.subplot(4, 1, 1)
    plt.plot(t_in, i_data_arr, drawstyle='steps-post', color='red')
    plt.title('Stage 1: Input PWM Signal')
    plt.xlim(0, view_window)
    plt.grid(True)

    # Plot 2: Truncated CIC Output
    plt.subplot(4, 1, 2)
    plt.plot(t_cic, cic_data_arr, color='orange', marker='.')
    plt.title(f'Stage 2: CIC Filter Output (Decimated by 100)')
    plt.xlim(0, view_window)
    plt.grid(True)

    # Plot 3: Compensation FIR Output
    plt.subplot(4, 1, 3)
    plt.plot(t_comp, comp_data_arr, color='green', marker='.')
    plt.title(f'Stage 3: Compensation FIR Output')
    plt.xlim(0, view_window)
    plt.grid(True)

    # Plot 4: Final Half-Band Decimator Output
    plt.subplot(4, 1, 4)
    plt.plot(t_final, o_data_arr, color='blue', marker='.')
    plt.title(f'Stage 4: Final Half-Band Output (Decimated by 200)')
    plt.xlim(0, view_window)
    plt.grid(True)

    plt.tight_layout()
    plt.savefig("decimation_stages.png")
    print("Plot successfully saved to decimation_stages.png")
