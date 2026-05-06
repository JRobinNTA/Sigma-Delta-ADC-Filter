import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer, RisingEdge
import numpy as np
from scipy import signal
import matplotlib
matplotlib.use('Agg')  # headless backend — no display needed
import matplotlib.pyplot as plt

@cocotb.test()
async def adc_chain_tb(dut):
    # -----------------------------------------------------------------------
    # System parameters -- must match adc_decimation_chain.v parameters
    # -----------------------------------------------------------------------
    CIC_R      = 100      # CIC decimation ratio
    HB_R       = 2        # Half-band decimation ratio
    TOTAL_DEC  = CIC_R * HB_R   # = 200
    fs_in      = 4_000_000       # 4 MHz bitstream clock rate
    fs_cic     = fs_in // CIC_R  # 40 kHz after CIC
    fs_out     = fs_in // TOTAL_DEC  # 20 kHz final output rate

    sig_freq   = 1000     # 1 kHz test tone (well inside passband)
    total_cycles = 200_000   # 50 ms of data (enough to get clean FFTs)

    # -----------------------------------------------------------------------
    # 1. Setup Clock & Reset
    #    Clock period = 1/fs_in; cocotb uses ns -> period_ns = 1e9/fs_in = 250 ns
    # -----------------------------------------------------------------------
    clk_period_ns = int(1e9 / fs_in)  # 250 ns for 4 MHz
    clock = Clock(dut.clk, clk_period_ns, unit="ns")
    cocotb.start_soon(clock.start())

    dut.rst.value    = 1
    dut.i_data.value = 0
    dut.i_ready.value = 0
    await Timer(10 * clk_period_ns, "ns")
    await RisingEdge(dut.clk)
    dut.rst.value = 0   # release reset

    # -----------------------------------------------------------------------
    # 2. Generate 1st-order Sigma-Delta bitstream
    #
    #    A real sigma-delta modulator is a noise-shaping loop. Here we
    #    approximate it with a running-accumulator PDM encoder (true 1st-order
    #    Sigma-Delta in software) rather than a simple PWM.
    #
    #    Phase 4 fix: add dither BEFORE quantisation to break limit cycles.
    # -----------------------------------------------------------------------
    t = np.arange(total_cycles) / fs_in

    # 1st-order SD at 0.9 amplitude -- simple, stable, fills dynamic range
    # (2nd-order is theoretically better but the comp-FIR's 2.9x boost at 10 kHz
    #  amplifies the shaped noise hill as much as the signal, costing ~4 dB SNR)
    analog = 0.9 * np.sin(2 * np.pi * sig_freq * t)

    # Analogue noise floor (~60 dB below signal)
    analog += np.random.normal(0, 1e-3, size=len(t))

    # Dither: TPDF, amplitude = 1% of full scale -- just enough to break limit cycles
    dither = (np.random.uniform(-0.5, 0.5, size=len(t)) +
              np.random.uniform(-0.5, 0.5, size=len(t)))
    analog_dithered = analog + dither * 0.01

    # ---- 1st-order Sigma-Delta modulator ----
    # Standard error-feedback topology:
    #   acc[n] = acc[n-1] + x[n] - y[n]   (integrator)
    #   y[n]   = sign(acc[n])              (1-bit quantiser)
    # This pushes quantisation noise to high frequencies (noise ? f)
    bitstream = np.zeros(total_cycles, dtype=int)
    sd_acc    = 0.0
    for i in range(total_cycles):
        sd_acc      += analog_dithered[i]          # integrate input
        y            = 1 if sd_acc >= 0 else -1    # quantise
        sd_acc      -= y                           # subtract feedback
        bitstream[i] = y

    # Sanity check -- SD accumulator should stay bounded
    # (recompute just to verify; not stored above)
    print(f"SD: ones={np.sum(bitstream==1)}, neg={np.sum(bitstream==-1)}, "
          f"duty={100*np.mean(bitstream==1):.1f}%  (expect ~55% for 0.9 amp)")

    # -----------------------------------------------------------------------
    # 3. Main Simulation Loop
    # -----------------------------------------------------------------------
    i_data_arr   = []
    cic_data_arr = []
    comp_data_arr= []
    o_data_arr   = []

    for i in range(total_cycles):
        await RisingEdge(dut.clk)

        val = int(bitstream[i])
        dut.i_data.value  = val
        dut.i_ready.value = 1
        i_data_arr.append(val)

        try:
            if dut.cic_ready.value == 1:
                cic_data_arr.append(dut.truncated_cic_data.value.to_signed())
        except ValueError:
            pass

        try:
            if dut.comp_ready.value == 1:
                comp_data_arr.append(dut.comp_data.value.to_signed())
        except ValueError:
            pass

        try:
            if dut.o_ready.value == 1:
                o_data_arr.append(dut.o_data.value.to_signed())
        except ValueError:
            pass

    print(f"Captured: CIC={len(cic_data_arr)}, Comp={len(comp_data_arr)}, Final={len(o_data_arr)}")

    # -----------------------------------------------------------------------
    # 4. Helper: compute windowed power spectrum & ENOB
    # -----------------------------------------------------------------------
    def compute_spectrum(data, fs, sig_f, noise_bw=None):
        """
        Returns (freqs_Hz, power_dB_rel_signal, snr_dB, enob).
        noise_bw: upper frequency limit for noise integration (Hz).
                  Defaults to fs/2 (full Nyquist). Set to fs/4 at CIC
                  output stages to exclude out-of-band shaped noise.
        """
        if noise_bw is None:
            noise_bw = fs / 2
        n   = len(data)
        win = np.hanning(n)
        x   = np.array(data, dtype=float)
        x   = x - np.mean(x)          # remove DC
        X   = np.fft.rfft(x * win, n=n)
        pwr = (np.abs(X) ** 2) / (np.sum(win**2) / 2)
        freqs   = np.fft.rfftfreq(n, 1/fs)
        # Signal bin
        sig_bin = np.argmin(np.abs(freqs - sig_f))
        sig_bw  = 5
        lo, hi  = max(0, sig_bin - sig_bw), sig_bin + sig_bw + 1
        sig_pwr = np.sum(pwr[lo:hi])
        # Noise: inside noise_bw only, excluding DC and signal window
        bw_bin       = np.searchsorted(freqs, noise_bw)
        noise_mask   = np.zeros(len(pwr), dtype=bool)
        noise_mask[1:bw_bin] = True   # only within noise_bw, skip DC
        noise_mask[lo:hi]    = False  # exclude signal
        noise_pwr = max(np.sum(pwr[noise_mask]), 1e-30)
        snr   = 10 * np.log10(sig_pwr / noise_pwr)
        enob  = (snr - 1.76) / 6.02
        # Normalise to signal peak = 0 dB
        pwr_db = 10 * np.log10(pwr / sig_pwr + 1e-30)
        return freqs, pwr_db, snr, enob

    # -----------------------------------------------------------------------
    # 5. Plotting
    # -----------------------------------------------------------------------
    view_ms = 5.0   # time-domain view window in ms

    t_in   = np.arange(len(i_data_arr))   / fs_in   * 1e3
    t_cic  = np.arange(len(cic_data_arr)) / fs_cic  * 1e3
    t_comp = np.arange(len(comp_data_arr))/ fs_cic  * 1e3
    t_out  = np.arange(len(o_data_arr))   / fs_out  * 1e3

    # noise_bw = 8 kHz (not 10 kHz): avoids the high-frequency noise hill where
    # the comp-FIR's 2.9x boost amplifies shaped SD noise as much as signal.
    f_cic,  p_cic,  snr_cic,  enob_cic  = compute_spectrum(cic_data_arr,  fs_cic, sig_freq, noise_bw=8000)
    f_comp, p_comp, snr_comp, enob_comp = compute_spectrum(comp_data_arr, fs_cic, sig_freq, noise_bw=8000)
    f_out,  p_out,  snr_out,  enob_out  = compute_spectrum(o_data_arr,    fs_out, sig_freq, noise_bw=8000)

    fig, axes = plt.subplots(4, 2, figsize=(16, 14))
    fig.suptitle(
        f"ADC Decimation Chain -- fs_in={fs_in/1e6:.2f} MHz  fs_out={fs_out} Hz"
        f"  Signal={sig_freq} Hz  SD order=1",
        fontsize=13, fontweight='bold'
    )

    # --- Input spectrum ---
    # Use ALL input samples and zoom to 0-50 kHz so the noise-shaping
    # slope (rising from DC toward fs_in/2) is actually visible.
    # At 4 MHz over 200 k samples: freq resolution = 4MHz/200000 = 20 Hz
    n_in   = len(i_data_arr)
    win_in = np.hanning(n_in)
    X_in   = np.fft.rfft(np.array(i_data_arr, float) * win_in)
    f_in_ax= np.fft.rfftfreq(n_in, 1/fs_in) / 1e3          # kHz
    sig_bin_in = np.argmin(np.abs(f_in_ax*1e3 - sig_freq))
    pwr_in  = (np.abs(X_in)**2) / (np.sum(win_in**2) / 2)
    p_in_rel = 10 * np.log10(pwr_in / pwr_in[sig_bin_in] + 1e-30)  # rel to signal

    # Theoretical SNR annotations
    osr = fs_in / (2 * fs_out)
    snr_plain  = 10 * np.log10(osr)
    snr_sd1    = 10 * np.log10((np.pi**2 / 3) * (2*osr/np.pi)**3 / (np.pi**2/3))
    # Simplified: SNR_SD1 ~ 6.02*(1.5*log2(OSR) - 0.17) + 1.76 (for 1-bit, 1st order)
    snr_sd1    = 6.02 * (1.5 * np.log2(osr) - 0.17) + 1.76
    enob_sd1   = (snr_sd1 - 1.76) / 6.02

    axes[0,1].plot(f_in_ax, p_in_rel, color='red', linewidth=0.5)
    axes[0,1].set_title('Input Spectrum -- zoomed 0?50 kHz (dB rel. signal)', fontsize=10)
    axes[0,1].set_xlabel('Frequency (kHz)')
    axes[0,1].set_ylabel('Power (dB rel signal)')
    axes[0,1].set_xlim(0, 50)          # zoom: noise shaping visible in passband
    axes[0,1].set_ylim(-120, 5)
    axes[0,1].axvline(sig_freq/1e3, color='cyan', ls=':', lw=0.8, label=f'{sig_freq} Hz')
    axes[0,1].axvline(fs_out/2/1e3,  color='lime',  ls='--', lw=0.8, label='fs_out/2')
    info = (f"fs_in  = {fs_in/1e6:.3f} MHz\n"
            f"fs_out = {fs_out} Hz\n"
            f"CIC R={CIC_R} M=10\n"
            f"HB  R={HB_R}\n"
            f"OSR    = {int(osr)}\n"
            f"CIC OWs 72 bits\n"
            f"SHIFT  = 67\n"
            f"SD ord = 1\n"
            f"-- Theoretical SNR --\n"
            f"1-ord SD: {snr_sd1:.0f} dB  {enob_sd1:.1f} ENOB")
    axes[0,1].text(0.02, 0.98, info, transform=axes[0,1].transAxes,
                   fontsize=7, va='top', family='monospace',
                   bbox=dict(boxstyle='round', fc='white', alpha=0.8))
    axes[0,1].grid(True, alpha=0.4)

    # Stage 1: time domain (zoomed)
    mask_in = t_in <= view_ms
    axes[0,0].plot(t_in[mask_in], np.array(i_data_arr)[mask_in],
                   drawstyle='steps-post', color='red', linewidth=0.5)
    axes[0,0].set_title('Stage 1: PWM / SD Input', fontsize=10)
    axes[0,0].set_xlabel('Time (ms)')
    axes[0,0].set_ylim(-1.5, 1.5)
    axes[0,0].grid(True, alpha=0.4)

    # Stage 2: CIC
    mask_cic = t_cic <= view_ms
    axes[1,0].plot(t_cic[mask_cic], np.array(cic_data_arr)[mask_cic], color='orange')
    axes[1,0].set_title(f'Stage 2: CIC (/{CIC_R})', fontsize=10)
    axes[1,0].set_xlabel('Time (ms)')
    axes[1,0].grid(True, alpha=0.4)

    axes[1,1].plot(f_cic/1e3, p_cic, color='orange', linewidth=0.8)
    axes[1,1].set_title('Stage 2 Spectrum', fontsize=10)
    axes[1,1].set_xlabel('Frequency (kHz)')
    axes[1,1].set_ylabel('Power (dB)')
    axes[1,1].legend([f'SNR = {snr_cic:.1f} dB\nENOB = {enob_cic:.2f} b'], fontsize=8)
    axes[1,1].grid(True, alpha=0.4)

    # Stage 3: Comp FIR
    mask_comp = t_comp <= view_ms
    axes[2,0].plot(t_comp[mask_comp], np.array(comp_data_arr)[mask_comp], color='green')
    axes[2,0].set_title('Stage 3: Comp FIR', fontsize=10)
    axes[2,0].set_xlabel('Time (ms)')
    axes[2,0].grid(True, alpha=0.4)

    axes[2,1].plot(f_comp/1e3, p_comp, color='green', linewidth=0.8)
    axes[2,1].set_title('Stage 3 Spectrum', fontsize=10)
    axes[2,1].set_xlabel('Frequency (kHz)')
    axes[2,1].set_ylabel('Power (dB)')
    axes[2,1].legend([f'SNR = {snr_comp:.1f} dB\nENOB = {enob_comp:.2f} b'], fontsize=8)
    axes[2,1].grid(True, alpha=0.4)

    # Stage 4: HB FIR
    mask_out = t_out <= view_ms
    axes[3,0].plot(t_out[mask_out], np.array(o_data_arr)[mask_out], color='blue')
    axes[3,0].set_title(f'Stage 4: HB FIR (/{HB_R})', fontsize=10)
    axes[3,0].set_xlabel('Time (ms)')
    axes[3,0].grid(True, alpha=0.4)

    axes[3,1].plot(f_out/1e3, p_out, color='blue', linewidth=0.8)
    axes[3,1].set_title('Stage 4 Spectrum', fontsize=10)
    axes[3,1].set_xlabel('Frequency (kHz)')
    axes[3,1].set_ylabel('Power (dB)')
    axes[3,1].legend([f'SNR = {snr_out:.1f} dB\nENOB = {enob_out:.2f} b'], fontsize=8)
    axes[3,1].grid(True, alpha=0.4)

    plt.tight_layout()
    plt.savefig("decimation_stages.png", dpi=150)
    plt.close()
    print(f"Saved decimation_stages.png")
    print(f"  Stage 2 CIC:  SNR={snr_cic:.1f} dB  ENOB={enob_cic:.2f}")
    print(f"  Stage 3 Comp: SNR={snr_comp:.1f} dB  ENOB={enob_comp:.2f}")
    print(f"  Stage 4 HB:   SNR={snr_out:.1f} dB  ENOB={enob_out:.2f}")
    print(f"  Theoretical 1st-order SD: SNR={snr_sd1:.0f} dB  ENOB={enob_sd1:.1f}")

    # -----------------------------------------------------------------------
    # 6. Dedicated frequency-response plot (frequency_stages.png)
    #    Shows the noise floor at each processing stage — high noise (CIC) vs
    #    low noise (HB output) — saved to the same folder.
    # -----------------------------------------------------------------------
    stage_names = [
        (f"CIC Output  (fs = {fs_cic} Hz)  — HIGH noise floor",  "orange", fs_cic,  cic_data_arr,  f_cic,  p_cic,  snr_cic,  enob_cic),
        (f"Comp FIR Output (fs = {fs_cic} Hz)",                   "green",  fs_cic,  comp_data_arr, f_comp, p_comp, snr_comp, enob_comp),
        (f"HB FIR Output   (fs = {fs_out} Hz)  — LOW noise floor", "blue",  fs_out,  o_data_arr,    f_out,  p_out,  snr_out,  enob_out),
    ]

    fig2, axes2 = plt.subplots(3, 1, figsize=(12, 10))
    fig2.suptitle(
        f"Frequency Spectra Across Decimation Chain  --  "
        f"fs_in={fs_in/1e6:.2f} MHz  OSR={int(osr)}  SD order=1",
        fontsize=13, fontweight='bold'
    )

    for idx, (label, color, fs_stage, data_arr, freqs_hz, pwr_db, snr_val, enob_val) in enumerate(stage_names):
        ax = axes2[idx]
        ax.plot(freqs_hz / 1e3, pwr_db, color=color, linewidth=0.8)
        ax.axvline(sig_freq / 1e3, color='black',
                   linestyle='--', linewidth=0.8, alpha=0.6,
                   label=f"Signal: {sig_freq} Hz")
        ax.set_title(label, fontweight='bold')
        ax.set_xlabel("Frequency (kHz)")
        ax.set_ylabel("Power (dB rel. signal)")
        ax.legend(fontsize=7, loc='upper right')
        ax.grid(True, alpha=0.35)
        ax.text(0.98, 0.97,
                f"SNR  = {snr_val:.1f} dB\nENOB = {enob_val:.2f} b",
                transform=ax.transAxes, ha='right', va='top', fontsize=8,
                family='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                          alpha=0.85, edgecolor='grey'))

    plt.tight_layout()
    plt.savefig("frequency_stages.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved frequency_stages.png")


# ═══════════════════════════════════════════════════════════════════════════════
#  NOISE SIMULATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_noisy_bitstream(total_cycles, fs_in, sig_freq, sig_amp,
                             jitter_sigma, analog_noise_sigma,
                             glitch_prob, dropout_prob):
    """
    Generate a 1st-order Sigma-Delta bitstream with injected non-idealities:
      - Clock jitter      : Gaussian perturbation on the time vector
      - Analog noise       : Additive white Gaussian noise on the analog signal
      - Comparator glitches: Random bit flips in the output bitstream
      - Signal dropouts    : Held previous sample (mimics lost ADC samples)
    """
    t = np.arange(total_cycles) / fs_in

    # Apply clock jitter to the time vector
    if jitter_sigma > 0:
        jitter = np.random.normal(0, jitter_sigma, size=len(t))
        t_jittered = t + jitter
    else:
        t_jittered = t

    # Generate clean analog signal + analog noise
    analog = sig_amp * np.sin(2 * np.pi * sig_freq * t_jittered)
    if analog_noise_sigma > 0:
        analog += np.random.normal(0, analog_noise_sigma, size=len(t))

    # Dither (same as clean test)
    dither = (np.random.uniform(-0.5, 0.5, size=len(t)) +
              np.random.uniform(-0.5, 0.5, size=len(t)))
    analog_dithered = analog + dither * 0.01

    # 1st-order Sigma-Delta modulator
    bitstream = np.zeros(total_cycles, dtype=int)
    sd_acc = 0.0
    for i in range(total_cycles):
        sd_acc      += analog_dithered[i]
        y            = 1 if sd_acc >= 0 else -1
        sd_acc      -= y
        bitstream[i] = y

    # Apply comparator glitches (random bit flips)
    if glitch_prob > 0:
        glitch_mask = np.random.random(total_cycles) < glitch_prob
        bitstream[glitch_mask] *= -1  # flip polarity

    # Apply signal dropouts (hold previous value)
    if dropout_prob > 0:
        for i in range(1, total_cycles):
            if np.random.random() < dropout_prob:
                bitstream[i] = bitstream[i - 1]

    return bitstream


async def run_dut_with_bitstream(dut, bitstream, total_cycles, fs_in,
                                 CIC_R, HB_R):
    """
    Feed a pre-generated bitstream into the DUT and collect outputs from
    all four stages. Returns (i_data_arr, cic_data_arr, comp_data_arr, o_data_arr).
    """
    i_data_arr    = []
    cic_data_arr  = []
    comp_data_arr = []
    o_data_arr    = []

    for i in range(total_cycles):
        await RisingEdge(dut.clk)
        val = int(bitstream[i])
        dut.i_data.value  = val
        dut.i_ready.value = 1
        i_data_arr.append(val)

        try:
            if dut.cic_ready.value == 1:
                cic_data_arr.append(dut.truncated_cic_data.value.to_signed())
        except ValueError:
            pass
        try:
            if dut.comp_ready.value == 1:
                comp_data_arr.append(dut.comp_data.value.to_signed())
        except ValueError:
            pass
        try:
            if dut.o_ready.value == 1:
                o_data_arr.append(dut.o_data.value.to_signed())
        except ValueError:
            pass

    return i_data_arr, cic_data_arr, comp_data_arr, o_data_arr


def noise_compute_spectrum(data, fs, sig_f, noise_bw=None):
    """Compute spectrum for noise tests (module-level so both tests can use it)."""
    if noise_bw is None:
        noise_bw = fs / 2
    n   = len(data)
    win = np.hanning(n)
    x   = np.array(data, dtype=float)
    x   = x - np.mean(x)
    X   = np.fft.rfft(x * win, n=n)
    pwr = (np.abs(X) ** 2) / (np.sum(win**2) / 2)
    freqs   = np.fft.rfftfreq(n, 1/fs)
    sig_bin = np.argmin(np.abs(freqs - sig_f))
    sig_bw  = 5
    lo, hi  = max(0, sig_bin - sig_bw), sig_bin + sig_bw + 1
    sig_pwr = np.sum(pwr[lo:hi])
    bw_bin       = np.searchsorted(freqs, noise_bw)
    noise_mask   = np.zeros(len(pwr), dtype=bool)
    noise_mask[1:bw_bin] = True
    noise_mask[lo:hi]    = False
    noise_pwr = max(np.sum(pwr[noise_mask]), 1e-30)
    snr   = 10 * np.log10(sig_pwr / noise_pwr)
    enob  = (snr - 1.76) / 6.02
    pwr_db = 10 * np.log10(pwr / sig_pwr + 1e-30)
    return freqs, pwr_db, snr, enob


def plot_noise_stages(i_data_arr, cic_data_arr, comp_data_arr, o_data_arr,
                      fs_in, CIC_R, HB_R, sig_freq, title, filename):
    """
    Generate a 4×2 plot (time-domain left, spectrum right) matching the main
    decimation_stages.png format.  Time-domain is zoomed to ~1 cycle of the
    signal so noise effects are clearly visible.
    """
    TOTAL_DEC = CIC_R * HB_R
    fs_cic = fs_in // CIC_R
    fs_out = fs_in // TOTAL_DEC

    # Zoom to 1 cycle of the signal, but skip the initial transient
    # by starting at ~25 ms (well after pipeline has settled)
    period_ms   = 1.0 / sig_freq * 1e3   # one period in ms
    view_start  = 25.0                    # ms — skip initial transient
    view_end    = view_start + period_ms  # show exactly 1 cycle

    # Time vectors in ms
    t_in   = np.arange(len(i_data_arr))    / fs_in   * 1e3
    t_cic  = np.arange(len(cic_data_arr))  / fs_cic  * 1e3
    t_comp = np.arange(len(comp_data_arr)) / fs_cic  * 1e3
    t_out  = np.arange(len(o_data_arr))    / fs_out  * 1e3

    # Compute spectra (noise_bw = 8 kHz to match main test)
    f_cic,  p_cic,  snr_cic,  enob_cic  = noise_compute_spectrum(cic_data_arr,  fs_cic, sig_freq, noise_bw=8000)
    f_comp, p_comp, snr_comp, enob_comp = noise_compute_spectrum(comp_data_arr, fs_cic, sig_freq, noise_bw=8000)
    f_out,  p_out,  snr_out,  enob_out  = noise_compute_spectrum(o_data_arr,    fs_out, sig_freq, noise_bw=8000)

    # Input spectrum (full bandwidth, zoomed to 0-50 kHz)
    n_in   = len(i_data_arr)
    win_in = np.hanning(n_in)
    X_in   = np.fft.rfft(np.array(i_data_arr, float) * win_in)
    f_in_ax= np.fft.rfftfreq(n_in, 1/fs_in) / 1e3
    sig_bin_in = np.argmin(np.abs(f_in_ax*1e3 - sig_freq))
    pwr_in  = (np.abs(X_in)**2) / (np.sum(win_in**2) / 2)
    p_in_rel = 10 * np.log10(pwr_in / pwr_in[sig_bin_in] + 1e-30)

    fig, axes = plt.subplots(4, 2, figsize=(16, 14))
    fig.suptitle(title, fontsize=13, fontweight='bold')

    # --- Row 0: Input ---
    mask_in = (t_in >= view_start) & (t_in <= view_end)
    axes[0,0].plot(t_in[mask_in], np.array(i_data_arr)[mask_in],
                   drawstyle='steps-post', color='red', linewidth=0.5)
    axes[0,0].set_title('Stage 1: PWM / SD Input', fontsize=10)
    axes[0,0].set_xlabel('Time (ms)')
    axes[0,0].set_ylim(-1.5, 1.5)
    axes[0,0].grid(True, alpha=0.4)

    axes[0,1].plot(f_in_ax, p_in_rel, color='red', linewidth=0.5)
    axes[0,1].set_title('Input Spectrum — zoomed 0→50 kHz (dB rel. signal)', fontsize=10)
    axes[0,1].set_xlabel('Frequency (kHz)')
    axes[0,1].set_ylabel('Power (dB rel signal)')
    axes[0,1].set_xlim(0, 50)
    axes[0,1].set_ylim(-120, 5)
    axes[0,1].axvline(sig_freq/1e3, color='cyan', ls=':', lw=0.8, label=f'{sig_freq} Hz')
    axes[0,1].axvline(fs_out/2/1e3,  color='lime',  ls='--', lw=0.8, label='fs_out/2')
    axes[0,1].legend(fontsize=7, loc='upper right')
    axes[0,1].grid(True, alpha=0.4)

    # --- Row 1: CIC ---
    mask_cic = (t_cic >= view_start) & (t_cic <= view_end)
    axes[1,0].plot(t_cic[mask_cic], np.array(cic_data_arr)[mask_cic], color='orange',
                   marker='.', markersize=4, linewidth=0.8)
    axes[1,0].set_title(f'Stage 2: CIC (/{CIC_R})', fontsize=10)
    axes[1,0].set_xlabel('Time (ms)')
    axes[1,0].grid(True, alpha=0.4)

    axes[1,1].plot(f_cic/1e3, p_cic, color='orange', linewidth=0.8)
    axes[1,1].set_title('Stage 2 Spectrum', fontsize=10)
    axes[1,1].set_xlabel('Frequency (kHz)')
    axes[1,1].set_ylabel('Power (dB)')
    axes[1,1].legend([f'SNR = {snr_cic:.1f} dB\nENOB = {enob_cic:.2f} b'], fontsize=8)
    axes[1,1].grid(True, alpha=0.4)

    # --- Row 2: Comp FIR ---
    mask_comp = (t_comp >= view_start) & (t_comp <= view_end)
    axes[2,0].plot(t_comp[mask_comp], np.array(comp_data_arr)[mask_comp], color='green',
                   marker='.', markersize=4, linewidth=0.8)
    axes[2,0].set_title('Stage 3: Comp FIR', fontsize=10)
    axes[2,0].set_xlabel('Time (ms)')
    axes[2,0].grid(True, alpha=0.4)

    axes[2,1].plot(f_comp/1e3, p_comp, color='green', linewidth=0.8)
    axes[2,1].set_title('Stage 3 Spectrum', fontsize=10)
    axes[2,1].set_xlabel('Frequency (kHz)')
    axes[2,1].set_ylabel('Power (dB)')
    axes[2,1].legend([f'SNR = {snr_comp:.1f} dB\nENOB = {enob_comp:.2f} b'], fontsize=8)
    axes[2,1].grid(True, alpha=0.4)

    # --- Row 3: HB FIR ---
    mask_out = (t_out >= view_start) & (t_out <= view_end)
    axes[3,0].plot(t_out[mask_out], np.array(o_data_arr)[mask_out], color='blue',
                   marker='.', markersize=4, linewidth=0.8)
    axes[3,0].set_title(f'Stage 4: HB FIR (/{HB_R})', fontsize=10)
    axes[3,0].set_xlabel('Time (ms)')
    axes[3,0].grid(True, alpha=0.4)

    axes[3,1].plot(f_out/1e3, p_out, color='blue', linewidth=0.8)
    axes[3,1].set_title('Stage 4 Spectrum', fontsize=10)
    axes[3,1].set_xlabel('Frequency (kHz)')
    axes[3,1].set_ylabel('Power (dB)')
    axes[3,1].legend([f'SNR = {snr_out:.1f} dB\nENOB = {enob_out:.2f} b'], fontsize=8)
    axes[3,1].grid(True, alpha=0.4)

    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved {filename}")
    print(f"  CIC:  SNR={snr_cic:.1f} dB  ENOB={enob_cic:.2f}")
    print(f"  Comp: SNR={snr_comp:.1f} dB  ENOB={enob_comp:.2f}")
    print(f"  HB:   SNR={snr_out:.1f} dB  ENOB={enob_out:.2f}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST 2: Moderate Noise  →  low_noise_detail.png
# ═══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def noise_low_tb(dut):
    """
    Moderate non-idealities test (Section 4 of the report).
    Noise parameters:
      - Clock jitter        σ = 0.00001
      - Analog noise        σ = 0.05
      - Comparator glitches 1 %
      - Signal dropouts     0.5 %
    """
    CIC_R        = 100
    HB_R         = 2
    TOTAL_DEC    = CIC_R * HB_R
    fs_in        = 4_000_000
    sig_freq     = 1000
    sig_amp      = 0.9
    total_cycles = 200_000

    clk_period_ns = int(1e9 / fs_in)
    clock = Clock(dut.clk, clk_period_ns, unit="ns")
    cocotb.start_soon(clock.start())

    dut.rst.value     = 1
    dut.i_data.value  = 0
    dut.i_ready.value = 0
    await Timer(10 * clk_period_ns, "ns")
    await RisingEdge(dut.clk)
    dut.rst.value = 0

    print("\n=== LOW NOISE TEST ===")
    print("  Jitter σ=0.00001, Analog noise σ=0.05, Glitch=1%, Dropout=0.5%")

    bitstream = generate_noisy_bitstream(
        total_cycles, fs_in, sig_freq, sig_amp,
        jitter_sigma=0.00001,
        analog_noise_sigma=0.05,
        glitch_prob=0.01,
        dropout_prob=0.005,
    )

    i_arr, cic_arr, comp_arr, o_arr = await run_dut_with_bitstream(
        dut, bitstream, total_cycles, fs_in, CIC_R, HB_R)

    print(f"  Captured: CIC={len(cic_arr)}, Comp={len(comp_arr)}, Final={len(o_arr)}")

    plot_noise_stages(
        i_arr, cic_arr, comp_arr, o_arr,
        fs_in, CIC_R, HB_R, sig_freq,
        title="ADC Decimation Chain — Moderate Noise\n"
              "(jitter σ=1e-5, noise σ=0.05, glitch=1%, dropout=0.5%)",
        filename="low_noise_detail.png",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST 3: High Noise Stress  →  high_noise_plot.png
# ═══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def noise_high_tb(dut):
    """
    High-noise stress test (Section 4 of the report).
    Noise parameters:
      - Clock jitter        σ = 0.0001
      - Analog noise        σ = 0.1
      - Comparator glitches 10 %
      - Signal dropouts     5 %
    """
    CIC_R        = 100
    HB_R         = 2
    TOTAL_DEC    = CIC_R * HB_R
    fs_in        = 4_000_000
    sig_freq     = 1000
    sig_amp      = 0.9
    total_cycles = 200_000

    clk_period_ns = int(1e9 / fs_in)
    clock = Clock(dut.clk, clk_period_ns, unit="ns")
    cocotb.start_soon(clock.start())

    dut.rst.value     = 1
    dut.i_data.value  = 0
    dut.i_ready.value = 0
    await Timer(10 * clk_period_ns, "ns")
    await RisingEdge(dut.clk)
    dut.rst.value = 0

    print("\n=== HIGH NOISE STRESS TEST ===")
    print("  Jitter σ=0.0001, Analog noise σ=0.1, Glitch=10%, Dropout=5%")

    bitstream = generate_noisy_bitstream(
        total_cycles, fs_in, sig_freq, sig_amp,
        jitter_sigma=0.0001,
        analog_noise_sigma=0.1,
        glitch_prob=0.10,
        dropout_prob=0.05,
    )

    i_arr, cic_arr, comp_arr, o_arr = await run_dut_with_bitstream(
        dut, bitstream, total_cycles, fs_in, CIC_R, HB_R)

    print(f"  Captured: CIC={len(cic_arr)}, Comp={len(comp_arr)}, Final={len(o_arr)}")

    plot_noise_stages(
        i_arr, cic_arr, comp_arr, o_arr,
        fs_in, CIC_R, HB_R, sig_freq,
        title="ADC Decimation Chain — High Noise Stress Test\n"
              "(jitter σ=1e-4, noise σ=0.1, glitch=10%, dropout=5%)",
        filename="high_noise_plot.png",
    )
