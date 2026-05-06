module cic_scaler #(
    parameter IN_W  = 72,
    parameter OUT_W = 72,   // must equal IN_W; saturate module handles FIR_W narrowing
    parameter SHIFT = 36    // = ceil(M*log2(R)) - (FIR_W-1) = 67 - 31 = 36
)(
    input  wire signed [IN_W-1:0]  i_data,
    output wire signed [OUT_W-1:0] o_data
);

    // Round-to-nearest: add 0.5 LSB before truncating
    wire signed [IN_W-1:0] rounded =
        i_data + ({{(IN_W-1){1'b0}}, 1'b1} <<< (SHIFT - 1));

    // Arithmetic right-shift (sign-preserving)
    wire signed [IN_W-1:0] shifted =
        rounded >>> SHIFT;

    assign o_data = shifted[OUT_W-1:0];

endmodule
