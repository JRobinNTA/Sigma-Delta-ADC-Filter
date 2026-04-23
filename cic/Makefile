# Makefile

SIM = verilator
#COMPILE_ARGS = --trace
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(shell pwd)/*.v

# Change TOPLEVEL to match your new wrapper module name
TOPLEVEL = adc_decimation_chain

# Keep MODULE as your python file name (cic_tb.py)
MODULE = cic_tb

include $(shell cocotb-config --makefiles)/Makefile.sim
