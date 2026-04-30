import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer, RisingEdge
import numpy as np
from scipy import signal
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ═══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PARAMETERS  —  only edit this block
# ═══════════════════════════════════════════════════════════════════════════════
FS_IN_HZ    = 4_000_000   # Bitstream / ADC clock (Hz)
CIC_R       = 100         # CIC decimation ratio
CIC_M       = 10          # CIC order (number of stages)
HB_R        = 2           # Half-band decimation ratio
SIG_FREQ_HZ = 1_000       # Test-tone frequency (Hz)
SIG_AMP     = 0.5         # Signal amplitude (keep < 1.0 to avoid Σ∆ clipping)
SD_ORDER    = 1           # Sigma-delta modulator order (1, 2, or 3)

# Derived — do not touch
OSR        = CIC_R * HB_R
FS_CIC_HZ  = FS_IN_HZ / CIC_R          # After CIC decimation
FS_COMP_HZ = FS_CIC_HZ                 # Comp FIR runs at same rate
FS_OUT_HZ  = FS_IN_HZ / OSR            # Final output rate

# How many output samples we want (drives TOTAL_CYCLES)
TARGET_OUTPUT_SAMPLES = 4_000          # Gives clean FFT with 3600 samples after trim
TOTAL_CYCLES = int(TARGET_OUTPUT_SAMPLES * OSR * 1.15)  # +15% margin for pipeline flush

# ═══════════════════════════════════════════════════════════════════════════════
#  SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def sigma_delta(x_float, order=1):
    """
    Nth-order sigma-delta modulator.
    Input : float array in [-1, +1]
    Output: int array of {-1, +1}

    Order-1: single integrator in the loop
    Order-2: two cascaded integrators (noise shaped more aggressively)
    """
    n = len(x_float)
    out = np.zeros(n, dtype=np.int8)
    err = np.zeros(order)          # one error accumulator per order

    for i in range(n):
        # Feed-forward: sum input and all error states
        v = x_float[i] + err[0]
        out[i] = 1 if v >= 0 else -1

        # Update error states (cascade of integrators)
        quant_err = x_float[i] - out[i]
        err[0] += quant_err
        for k in range(1, order):
            err[k] += err[k - 1]

    return out.astype(int)


def make_test_signal(total_cycles, fs_in, sig_freq, amp, sd_order):
    t   = np.linspace(0, total_cycles / fs_in, total_cycles, endpoint=False)
    sig = amp * np.sin(2 * np.pi * sig_freq * t)
    bits = sigma_delta(sig, order=sd_order)
    return t, sig, bits


# ═══════════════════════════════════════════════════════════════════════════════
#  SNR / ENOB  (FFT-based, windowed)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_snr_enob(samples, fs, sig_freq, label=""):
    samples = np.array(samples, dtype=float)
    if len(samples) < 64:
        print(f"[{label}] Only {len(samples)} samples — skipping SNR.")
        return None

    # Discard first 10 % (pipeline transient)
    x = samples[len(samples) // 10:]
    N = len(x)

    win   = np.hanning(N)
    X     = np.fft.rfft(x * win)
    power = (2.0 / (N * np.sum(win ** 2))) * np.abs(X) ** 2
    freqs = np.fft.rfftfreq(N, d=1.0 / fs)

    bin_res = fs / N
    sig_bin = int(round(sig_freq / bin_res))

    # Guard band: ±5 Hz or ±3 bins, whichever is wider
    guard = max(3, int(np.ceil(5.0 / bin_res)))
    sig_bins   = set(range(max(0, sig_bin - guard),
                           min(len(power), sig_bin + guard + 1)))
    noise_bins = [i for i in range(1, len(power) - 1) if i not in sig_bins]

    P_sig   = float(np.sum(power[list(sig_bins)]))
    P_noise = float(np.sum(power[noise_bins]))

    if P_sig <= 0 or P_noise <= 0:
        print(f"[{label}] Degenerate power — Ps={P_sig:.2e}  Pn={P_noise:.2e}")
        return None

    snr_db = 10.0 * np.log10(P_sig / P_noise)
    enob   = (snr_db - 1.76) / 6.02

    print(f"[{label}]  N={N}  Ps={P_sig:.3e}  Pn={P_noise:.3e}"
          f"  SNR={snr_db:.2f} dB  ENOB={enob:.2f} bits")
    return dict(snr=snr_db, enob=enob, freqs=freqs, power=power, N=N)


# ═══════════════════════════════════════════════════════════════════════════════
#  THEORETICAL LIMITS
# ═══════════════════════════════════════════════════════════════════════════════

def theoretical_snr(osr, order):
    """Return (snr_db, enob) for an Nth-order Σ∆ with given OSR."""
    if order == 0:
        snr = 6.02 * 1 + 1.76 + 10 * np.log10(osr)
    else:
        snr = (6.02 + 1.76
               + 10 * (2 * order + 1) * np.log10(osr)
               - 10 * np.log10(np.pi ** (2 * order) / (2 * order + 1)))
    return snr, (snr - 1.76) / 6.02


def print_theoretical_table():
    cic_ow_min = int(np.ceil(1 + CIC_M * np.log2(CIC_R)))   # IW=1 for 1-bit
    cic_shift  = int(np.ceil(CIC_M * np.log2(CIC_R)))

    print("\n╔════════════════════════════════════════════════════════════╗")
    print( "║            ADC Decimation Chain — Parameters               ║")
    print( "╠════════════════════════════════════════════════════════════╣")
    print(f"║  fs_in      = {FS_IN_HZ/1e6:.3f} MHz                       ║")
    print(f"║  CIC  R={CIC_R}  M={CIC_M}  →  fs_cic = {FS_CIC_HZ:.0f} Hz ║")
    print(f"║  HB   R={HB_R}          →  fs_out = {FS_OUT_HZ:.0f} Hz     ║")
    print(f"║  OSR        = {OSR}                                        ║")
    print(f"║  CIC_OW min = {cic_ow_min} bits   CIC_SHIFT = {cic_shift}  ║")
    print( "╠════════════════════════════════════════════════════════════╣")
    print( "║  Theoretical SNR / ENOB                                    ║")
    for o in range(4):
        snr, enob = theoretical_snr(OSR, o)
        tag = "plain OS" if o == 0 else f"  {o}-ord Σ∆"
        mark = " ◄" if o == SD_ORDER else "  "
        print(f"║  {tag}:  {snr:6.1f} dB   {enob:5.2f} ENOB{mark}        ║")
    print( "╚════════════════════════════════════════════════════════════╝\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  PLOTTING
# ═══════════════════════════════════════════════════════════════════════════════

STAGE_CFG = [
    # (label,           color,    fs)
    ("PWM / Σ∆ Input",  "red",    FS_IN_HZ),
    (f"CIC  (÷{CIC_R})", "orange", FS_CIC_HZ),
    ("Comp FIR",        "green",  FS_COMP_HZ),
    (f"HB FIR (÷{HB_R})","blue",   FS_OUT_HZ),
]


def make_spectrum(arr, fs):
    x   = np.array(arr[len(arr) // 10:], dtype=float)
    N   = len(x)
    win = np.hanning(N)
    X   = np.fft.rfft(x * win)
    pwr = (2.0 / (N * np.sum(win ** 2))) * np.abs(X) ** 2
    pwr_db = 10.0 * np.log10(np.maximum(pwr, 1e-20))
    freqs  = np.fft.rfftfreq(N, d=1.0 / fs)
    return freqs, pwr_db


def annotate_snr(ax, result):
    if result is None:
        return
    ax.text(0.98, 0.97,
            f"SNR  = {result['snr']:.1f} dB\nENOB = {result['enob']:.2f} b",
            transform=ax.transAxes, ha='right', va='top', fontsize=8,
            family='monospace',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                      alpha=0.85, edgecolor='grey'))


def build_summary_text():
    cic_ow_min = int(np.ceil(1 + CIC_M * np.log2(CIC_R)))
    cic_shift  = int(np.ceil(CIC_M * np.log2(CIC_R)))
    lines = [
        f"fs_in   = {FS_IN_HZ/1e6:.3f} MHz",
        f"fs_out  = {FS_OUT_HZ:.0f} Hz",
        f"CIC  R={CIC_R}  M={CIC_M}",
        f"HB   R={HB_R}",
        f"OSR     = {OSR}",
        f"CIC_OW≥ {cic_ow_min} bits",
        f"SHIFT   = {cic_shift}",
        f"Σ∆ ord  = {SD_ORDER}",
        "",
        "── Theoretical SNR ──",
    ]
    for o in range(4):
        snr, enob = theoretical_snr(OSR, o)
        tag  = "plain OS" if o == 0 else f" {o}-ord Σ∆"
        mark = " ◄" if o == SD_ORDER else ""
        lines.append(f"{tag}: {snr:.0f} dB  {enob:.1f} ENOB{mark}")
    return "\n".join(lines)


def plot_results(arrays, results, view_cycles=5):
    """
    arrays : [i_data_arr, cic_data_arr, comp_data_arr, o_data_arr]
    results: [None, cic_result, comp_result, hb_result]   (None for input stage)
    """
    view_sec = view_cycles / SIG_FREQ_HZ

    fig = plt.figure(figsize=(18, 13))
    fig.suptitle(
        f"ADC Decimation Chain  —  fs_in={FS_IN_HZ/1e6:.2f} MHz  "
        f"fs_out={FS_OUT_HZ:.0f} Hz  Signal={SIG_FREQ_HZ} Hz  Σ∆ order={SD_ORDER}",
        fontsize=13, fontweight='bold')

    gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.52, wspace=0.30)

    for row, (arr, res, (stage_label, color, fs)) in enumerate(
            zip(arrays, results, STAGE_CFG)):

        t_vec = np.arange(len(arr)) / fs

        # ── Time-domain (left) ────────────────────────────────────────────────
        ax_t = fig.add_subplot(gs[row, 0])
        mask = t_vec <= view_sec
        # For the raw bitstream, show stepped; others show dots+line
        ds = 'steps-post' if row == 0 else 'default'
        ax_t.plot(t_vec[mask] * 1e3, np.array(arr)[mask],
                  color=color, drawstyle=ds,
                  linewidth=0.9, marker=(None if row == 0 else '.'),
                  markersize=3)
        ax_t.set_title(f"Stage {row+1}: {stage_label}", fontweight='bold')
        ax_t.set_xlabel("Time (ms)")
        ax_t.grid(True, alpha=0.35)

        # ── Spectrum (right) ──────────────────────────────────────────────────
        ax_f = fig.add_subplot(gs[row, 1])

        if row == 0:
            # Show input spectrum only up to fs_in/10 (very wide, less useful)
            freqs, pwr_db = make_spectrum(arr[:min(len(arr), 50_000)], fs)
            ax_f.plot(freqs / 1e3, pwr_db, color=color, linewidth=0.6)
            ax_f.set_xlim(0, fs / 2e3)
            ax_f.set_title("Input Spectrum (zoomed)", fontweight='bold')
        else:
            freqs, pwr_db = make_spectrum(arr, fs)
            ax_f.plot(freqs / 1e3, pwr_db, color=color, linewidth=0.8)
            # Mark signal frequency
            ax_f.axvline(SIG_FREQ_HZ / 1e3, color='black',
                         linestyle='--', linewidth=0.8, alpha=0.6,
                         label=f"{SIG_FREQ_HZ} Hz")
            ax_f.legend(fontsize=7, loc='upper right')
            ax_f.set_title(f"Stage {row+1} Spectrum", fontweight='bold')
            annotate_snr(ax_f, res)

        ax_f.set_xlabel("Frequency (kHz)")
        ax_f.set_ylabel("Power (dB)")
        ax_f.grid(True, alpha=0.35)

    # Summary box in top-right (replaces spectrum for input row)
    # Actually let's overlay it on the top-right axis
    ax_summary = fig.add_subplot(gs[0, 1])
    ax_summary.axis('off')
    ax_summary.text(0.04, 0.97, build_summary_text(),
                    transform=ax_summary.transAxes,
                    va='top', family='monospace', fontsize=8.5,
                    bbox=dict(boxstyle='round,pad=0.5',
                              facecolor='#f7f7f7', alpha=0.9,
                              edgecolor='#aaaaaa'))

    plt.savefig("decimation_stages.png", dpi=150, bbox_inches='tight')
    print("Plot saved → decimation_stages.png")


# ═══════════════════════════════════════════════════════════════════════════════
#  COCOTB TEST
# ═══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def adc_chain_tb(dut):
    print_theoretical_table()

    # ── Clock & Reset ─────────────────────────────────────────────────────────
    clock = Clock(dut.clk, 1, unit="ns")
    cocotb.start_soon(clock.start())

    dut.rst.value     = 1
    dut.i_data.value  = 0
    dut.i_ready.value = 0
    await Timer(10, "ns")
    await RisingEdge(dut.clk)
    dut.rst.value = 0

    # ── Generate bitstream ────────────────────────────────────────────────────
    print(f"Generating {TOTAL_CYCLES} samples  "
          f"(target ≈{TARGET_OUTPUT_SAMPLES} output samples) ...")

    t_arr, sig_arr, bits = make_test_signal(
        TOTAL_CYCLES, FS_IN_HZ, SIG_FREQ_HZ, SIG_AMP, SD_ORDER)

    # ── Collection ───────────────────────────────────────────────────────────
    i_data_arr    = []
    cic_data_arr  = []
    comp_data_arr = []
    o_data_arr    = []

    # ── Simulation loop ───────────────────────────────────────────────────────
    for i in range(TOTAL_CYCLES):
        await RisingEdge(dut.clk)

        dut.i_data.value  = int(bits[i])
        dut.i_ready.value = 1
        i_data_arr.append(int(bits[i]))

        if dut.cic_ready.value == 1:
            cic_data_arr.append(dut.truncated_cic_data.value.to_signed())
        if dut.comp_ready.value == 1:
            comp_data_arr.append(dut.comp_data.value.to_signed())
        if dut.o_ready.value == 1:
            o_data_arr.append(dut.o_data.value.to_signed())

    print(f"\nCaptured → CIC: {len(cic_data_arr)}  "
          f"Comp: {len(comp_data_arr)}  "
          f"HB: {len(o_data_arr)}")

    # ── SNR / ENOB at each stage ──────────────────────────────────────────────
    print("\n─── Measured SNR / ENOB ──────────────────────────────────")
    cic_snr  = compute_snr_enob(cic_data_arr,  FS_CIC_HZ,  SIG_FREQ_HZ, "CIC ")
    comp_snr = compute_snr_enob(comp_data_arr, FS_COMP_HZ, SIG_FREQ_HZ, "COMP")
    hb_snr   = compute_snr_enob(o_data_arr,    FS_OUT_HZ,  SIG_FREQ_HZ, "HB  ")
    print("──────────────────────────────────────────────────────────\n")

    # Print delta vs theoretical
    th_snr, th_enob = theoretical_snr(OSR, SD_ORDER)
    if hb_snr:
        delta = hb_snr['snr'] - th_snr
        print(f"Theoretical ({SD_ORDER}-ord Σ∆): {th_snr:.1f} dB  {th_enob:.2f} ENOB")
        print(f"Measured (HB output)        : {hb_snr['snr']:.1f} dB  {hb_snr['enob']:.2f} ENOB")
        print(f"Delta                       : {delta:+.1f} dB")
        if delta < -6:
            print("  ⚠  Large gap suggests CIC_SHIFT is wrong or output is saturating.")

    # ── Plot ──────────────────────────────────────────────────────────────────
    plot_results(
        arrays  = [i_data_arr, cic_data_arr, comp_data_arr, o_data_arr],
        results = [None, cic_snr, comp_snr, hb_snr],
        view_cycles = 5
    )
