"""
Generate CIC Compensation FIR coefficients for:
  CIC: R=100, M=10, IW=2
  CIC output rate: fs_cic = fs_in / R = 4MHz / 100 = 40 kHz
  Passband: 0 to 10 kHz  (= fs_cic / 4, since halfband decimates by 2)
  Filter length: 15 taps (symmetric, Q15 coefficients)

Run:  python3 generate_comp_fir.py
Then copy the printed coefficient values into comp_fir.v.
"""

import numpy as np
from scipy.signal import firls, freqz

# ---- System parameters ----
R        = 100
M        = 10
fs_in    = 4_000_000
fs_cic   = fs_in // R       # 40 000 Hz
fs_out   = fs_cic // 2      # 20 000 Hz  (after halfband ÷2)
f_pass   = 8000.0           # 8 kHz compensation edge (leave 8-10 kHz for HB FIR)
                            # Using 10 kHz caused 2.9× noise boost at passband edge
N_TAPS   = 21               # must be odd for Type-I symmetric FIR

# ---- Desired inverse-CIC response at frequency samples ----
N_pts   = 512
f_pts   = np.linspace(0, fs_cic / 2, N_pts)
f_norm  = f_pts / fs_cic     # 0 … 0.5

# CIC response (sinc approximation, valid for large R)
with np.errstate(divide='ignore', invalid='ignore'):
    sinc_val = np.where(f_norm == 0, 1.0,
                        np.sin(np.pi * f_norm) / (np.pi * f_norm))
H_cic = np.abs(sinc_val) ** M

# Desired compensation = inverse CIC in passband, 0 in stopband
f_pass_norm = f_pass / fs_cic          # 0.25
f_stop_norm = f_pass_norm + 0.05       # 0.30 — narrow transition band

H_desired = np.where(f_norm <= f_pass_norm, 1.0 / H_cic, 0.0)
# Smooth the transition band to avoid Gibbs
H_desired = np.where(f_norm > f_stop_norm, 0.0, H_desired)

# ---- Build frequency / desired-gain arrays for firls ----
# Use many sample points across passband so the least-squares fit is tight.
# firls API: bands in Hz (0..nyquist), desired gains at each band-edge pair.
nyquist  = fs_cic / 2.0
f_stop   = f_pass + 3000.0          # 13 kHz stop edge

# Dense passband samples for accurate inverse-sinc fit
n_pb  = 64
f_pb  = np.linspace(0, f_pass, n_pb)
with np.errstate(divide='ignore', invalid='ignore'):
    sinc_pb = np.where(f_pb == 0, 1.0,
               np.abs(np.sin(np.pi * f_pb / fs_cic) / (np.pi * f_pb / fs_cic)))
g_pb = 1.0 / sinc_pb ** M           # desired gain per frequency point

# Build firls band / desired vectors (alternating pairs)
# Passband: [0, f_pass] with inverse-sinc gain
# Stopband: [f_stop, nyquist] with 0 gain
bands_hz  = np.concatenate([[0], f_pb, [f_pass],
                             [f_stop, nyquist]])
desired_v = np.concatenate([[g_pb[0]], g_pb, [g_pb[-1]],
                             [0.0, 0.0]])

# firls requires alternating stop/pass specification in pairs;
# use the simpler 4-point specification with high passband weight
bands_simple  = [0,           f_pass,
                 f_stop,      nyquist]
desired_simple = [1.0, float(g_pb[-1]), 0.0, 0.0]
weight         = [100.0, 1.0]         # heavily prioritise passband flatness

h = firls(N_TAPS, bands_simple, desired_simple, weight=weight, fs=fs_cic)

# No window — firls is already least-squares optimal.
# Windowing suppresses the high-frequency taps needed for M=10 droop correction.

# ---- Normalise so peak coefficient fits in Q15 (no overflow) ----
peak    = np.max(np.abs(h))
h_norm  = h / peak        # now peak = 1.0

# ---- Quantise to Q15 ----
Q15_SCALE = 32767
h_q15 = np.round(h_norm * Q15_SCALE).astype(int)

# ---- Print results ----
print("=" * 60)
print(f"CIC Compensation FIR  (M={M}, R={R}, {N_TAPS} taps, Q15)")
print(f"  comp_fir.v delay_line must be 0:{N_TAPS-1}, center tap = {N_TAPS//2}")
print("=" * 60)
print("\nVerilog localparam block (paste into comp_fir.v):\n")
mid = N_TAPS // 2
for k in range(mid + 1):
    val  = h_q15[k]
    suffix = "  // center" if k == mid else ""
    sign_str = "-16'd" if val < 0 else " 16'd"
    print(f"    localparam signed [COEFF_W-1:0] C{k:2d} = {sign_str}{abs(val)};{suffix}")

# ---- Show frequency response ----
w, H = freqz(h_norm, worN=1024, fs=fs_cic)
H_comp_mag  = np.abs(H)
H_cic_at_w  = np.where(w == 0, 1.0,
               np.abs(np.sin(np.pi * w / fs_cic) /
                      (np.pi * w / fs_cic)) ** M)
H_total = H_comp_mag * H_cic_at_w

print("\nPassband flatness (combined CIC × Comp FIR):")
mask = w <= f_pass
print(f"  Max gain: {20*np.log10(np.max(H_total[mask])):.2f} dB")
print(f"  Min gain: {20*np.log10(np.min(H_total[mask])+1e-20):.2f} dB")
print(f"  Ripple  : {20*np.log10(np.max(H_total[mask])/np.min(H_total[mask])+1e-20):.2f} dB")

if __name__ == "__main__" and False:
    import matplotlib.pyplot as plt
    plt.figure(figsize=(10, 4))
    plt.plot(w/1e3, 20*np.log10(H_cic_at_w+1e-20), label='CIC alone')
    plt.plot(w/1e3, 20*np.log10(H_comp_mag+1e-20),   label='Comp FIR')
    plt.plot(w/1e3, 20*np.log10(H_total+1e-20),       label='Combined', lw=2)
    plt.axvline(f_pass/1e3, color='r', ls='--', label='Passband edge')
    plt.xlim(0, fs_cic/2/1e3); plt.ylim(-40, 10)
    plt.xlabel('Frequency (kHz)'); plt.ylabel('Magnitude (dB)')
    plt.legend(); plt.grid(True); plt.tight_layout(); plt.show()
