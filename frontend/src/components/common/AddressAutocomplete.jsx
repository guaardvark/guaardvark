import React, { useEffect, useMemo, useState } from "react";
import { Autocomplete, Box, CircularProgress, TextField, Typography } from "@mui/material";

import useDebounce from "../../hooks/useDebounce";
import { suggestAddresses } from "../../api/addressService";

/**
 * Free-text address field that suggests as you type.
 *
 * Suggestions are a convenience, never a constraint: the field is `freeSolo`,
 * so an address nobody has entered before is simply typed. Suggestions come
 * from the addresses already on file, plus a third-party provider when the
 * operator has configured one.
 *
 * `onChange` receives the address text. `onResolve`, when given, additionally
 * receives the whole suggestion so a form can fill its city/state/zip fields.
 *
 * @param {object} props
 * @param {string} props.value
 * @param {(text: string) => void} props.onChange
 * @param {(suggestion: object) => void} [props.onResolve]
 * @param {string} [props.label]
 */
const AddressAutocomplete = ({
  value = "",
  onChange,
  onResolve,
  label = "Address",
  placeholder,
  helperText,
  size = "small",
  disabled = false,
  required = false,
  error = false,
  fullWidth = true,
  sx,
  inputProps = {},
  "data-testid": dataTestId,
}) => {
  const [options, setOptions] = useState([]);
  const [attribution, setAttribution] = useState(null);
  const [inputText, setInputText] = useState(value || "");
  const [loading, setLoading] = useState(false);
  const debounced = useDebounce(inputText, 300);

  useEffect(() => {
    setInputText(value || "");
  }, [value]);

  useEffect(() => {
    let cancelled = false;
    const query = (debounced || "").trim();
    if (query.length < 2) {
      setOptions([]);
      setAttribution(null);
      return undefined;
    }
    setLoading(true);
    suggestAddresses({ q: query })
      .then(({ items, attribution: credit }) => {
        if (cancelled) return;
        setOptions(items);
        setAttribution(credit);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  const labelOf = useMemo(
    () => (option) =>
      typeof option === "string" ? option : option?.label || option?.address || "",
    [],
  );

  const handleChange = (_event, next) => {
    if (!next) {
      onChange?.("");
      return;
    }
    if (typeof next === "string") {
      onChange?.(next);
      return;
    }
    onChange?.(next.label || next.address || "");
    onResolve?.(next);
  };

  return (
    <Autocomplete
      data-testid={dataTestId}
      freeSolo
      options={options}
      // The server already matched; re-filtering here would drop provider rows
      // whose formatting differs from what was typed.
      filterOptions={(opts) => opts}
      inputValue={inputText}
      onInputChange={(_e, next, reason) => {
        setInputText(next);
        if (reason === "input") onChange?.(next);
      }}
      onChange={handleChange}
      getOptionLabel={labelOf}
      loading={loading}
      disabled={disabled}
      fullWidth={fullWidth}
      size={size}
      sx={sx}
      openOnFocus
      handleHomeEndKeys
      renderOption={(props, option) => {
        const { key, ...rest } = props;
        return (
          <Box component="li" key={key} {...rest}>
            <Box>
              <Typography variant="body2">{labelOf(option)}</Typography>
              {option?.source ? (
                <Typography variant="caption" color="text.secondary">
                  {option.source}
                </Typography>
              ) : null}
            </Box>
          </Box>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder={placeholder}
          required={required}
          error={error}
          helperText={helperText ?? (attribution || " ")}
          inputProps={{ ...params.inputProps, ...inputProps }}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {loading ? <CircularProgress size={18} /> : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
    />
  );
};

export default AddressAutocomplete;
