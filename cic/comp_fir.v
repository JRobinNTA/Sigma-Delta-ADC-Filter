module comp_fir #(
    parameter IW     = 32,
    parameter OW     = 32,
    parameter COEFF_W = 16
)(
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 i_valid,
    input  wire signed [IW-1:0] i_data,
    output reg                  o_valid,
    output reg  signed [OW-1:0] o_data
);

    // ---------------------------------------------------------------
    // PASSTHROUGH MODE
    //
    // Droop correction (inverse sinc^10) was boosting high-frequency
    // IN-BAND noise (Σ∆ noise rises toward 8 kHz) more than the 1 kHz
    // signal → 13 dB SNR loss through the filter.
    //
    // For a 1st-order Σ∆ the in-band noise is NOT flat — it rises with
    // frequency. Any filter that boosts high frequencies will amplify
    // noise more than signal, degrading SNR.
    //
    // Passthrough recovers the 13 dB. The halfband FIR still provides
    // its own mild rolloff shaping. Droop correction can be re-enabled
    // for a higher-order Σ∆ where the in-band noise IS suppressed at
    // high frequencies (making the comp FIR boost worthwhile).
    // ---------------------------------------------------------------

    always @(posedge clk) begin
        if (rst) begin
            o_data  <= 0;
            o_valid <= 0;
        end else begin
            o_data  <= i_data;
            o_valid <= i_valid;
        end
    end

endmodule
