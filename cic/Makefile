# Makefile

SIM           = verilator
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(shell pwd)/*.v

TOPLEVEL = adc_decimation_chain
MODULE   = cic_tb

# cocotb is installed in the pyenv 3.12.2 environment
COCOTB_CONFIG = /home/sanad/.pyenv/versions/3.12.2/bin/cocotb-config

include $(shell $(COCOTB_CONFIG) --makefiles)/Makefile.sim
