import numpy as np
from scipy import signal

fs = 40000
t = np.arange(10000) / fs
sig = 1.3e9 * np.sin(2 * np.pi * 1000 * t)
noise = np.random.normal(0, 1.3e9 * 10**(-100/20), len(t))
x = sig + noise

# Perfect brick-wall filter
b = signal.firwin(1001, 0.45) # cutoff at 0.45 * fs/2 = 9 kHz
x_filtered = signal.lfilter(b, 1, x)

# Decimate by 2
y = x_filtered[::2]

def get_snr(data, fs):
    win = np.hanning(len(data))
    X = np.fft.rfft(data * win)
    pwr = np.abs(X)**2
    freqs = np.fft.rfftfreq(len(data), 1/fs)
    sig_bin = np.argmax(pwr)
    sig_pwr = np.sum(pwr[sig_bin-2:sig_bin+3])
    noise_mask = np.ones(len(pwr), dtype=bool)
    noise_mask[0] = False
    noise_mask[sig_bin-2:sig_bin+3] = False
    bw_bin = np.searchsorted(freqs, 8000)
    noise_mask[bw_bin:] = False
    noise_pwr = np.sum(pwr[noise_mask])
    return 10 * np.log10(sig_pwr / noise_pwr)

print(f"Input SNR: {get_snr(x, fs):.2f} dB")
print(f"Output SNR (perfect filter + decimate): {get_snr(y, fs/2):.2f} dB")
